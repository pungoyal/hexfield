"""Local web server for the browser UI (standard library only).

    python3 -m hexfield.web              # opens http://127.0.0.1:8765
    python3 -m hexfield.web --port 9000 --no-browser
    python3 -m hexfield.web --lan        # let other devices on your network join online games

The server can run several games at once. A browser names its game with ?g=<id> and
proves its seat with ?t=<token>; without a token a request from this machine acts for
the first human seat, so a single-player game needs no ceremony. Starting, resuming,
listing and deleting games is only allowed from this machine: someone who joins over
the network can play in the game they were invited to and nothing else.
"""

import argparse
import atexit
import json
import mimetypes
import os
import socket
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .. import store
from ..game import IllegalAction
from ..record import Record
from .session import MODES, Replay, WebGame

MAX_BODY = 64 * 1024
MAX_IMPORT = 4 * 1024 * 1024
MAX_LIVE = 24           # live games kept in memory; the rest wait on disk
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
LOCAL = ("127.0.0.1", "::1", "::ffff:127.0.0.1")
HOST_ONLY = {"new", "games", "resume", "delete", "stats", "replay", "replay_hint", "export", "import", "branch"}


class Forbidden(Exception):
    pass


class App:
    """The games this server is hosting."""

    def __init__(self):
        self.games = {}     # gid -> WebGame
        self.replays = {}   # gid -> Replay
        self.game = None    # the newest game: what a request without ?g= means
        self.lock = threading.Lock()
        self.persist = True
        self.lan = None     # this machine's network address when others can reach the server
        atexit.register(self.close)

    def close(self):
        for g in list(self.games.values()):
            g.close()

    def add(self, game):
        with self.lock:
            self.games[game.gid] = game
            self.game = game
            idle = sorted(self.games.values(), key=lambda g: g.touched)
            drop = idle[:max(0, len(idle) - MAX_LIVE)]
            for g in drop:
                del self.games[g.gid]
        for g in drop:
            g.close()
        return game

    def get(self, gid, disk=True):
        """A live game by id; one that is only on disk is brought back first."""
        if not gid:
            return self.game
        g = self.games.get(gid)
        if g is None and self.persist and disk:
            try:
                g = WebGame.resume(gid)
            except (OSError, ValueError, KeyError):
                return None
            self.add(g)
        return g

    def new(self, body):
        def num(key, default=None):
            v = body.get(key, default)
            try:
                return int(v) if v not in (None, "") else default
            except (TypeError, ValueError):
                raise IllegalAction(f"{key} must be a number")

        opp, seed, seat = num("opponents", 3), num("seed"), num("seat")
        mode = body.get("mode", "solo")
        if mode not in MODES:
            raise IllegalAction("unknown mode")
        humans = None
        if mode != "solo":
            names = body.get("names")
            count = num("humans", len(names) if isinstance(names, list) else 2)
            if not 2 <= count <= 6:
                raise IllegalAction("2 to 6 human players")
            names = names if isinstance(names, list) else []
            humans = [str(names[i]) if i < len(names) and names[i] else
                      (body.get("name") or "Host" if i == 0 else f"Player {i + 1}") for i in range(count)]
            seat = None
        if not 0 <= opp <= 5 or not 3 <= opp + len(humans or [1]) <= 6:
            raise IllegalAction("a game has 3 to 6 players")
        seat = seat or None
        if seat is not None and not 1 <= seat <= opp + 1:
            raise IllegalAction("seat out of range")
        game = WebGame(opp, body.get("level", "hard"), seed, seat, body.get("name") or "You",
                       review=bool(body.get("review", True)), styles=body.get("styles", "mixed"),
                       dice=body.get("dice", "random"), layout=body.get("layout", "random"),
                       humans=humans, mode=mode, persist=self.persist)
        if mode == "online":
            game.save()  # the seat keys must survive a restart; other games are saved at the first move
        return self.add(game)

    def replay(self, gid):
        r = self.replays.get(gid)
        live = self.games.get(gid)
        if live is not None:
            live.save()
        if r is None or (live is not None and len(live.rec.entries) != len(r.rec.entries)):
            r = Replay(gid)
            with self.lock:
                self.replays[gid] = r
                for old in sorted(self.replays.values(), key=lambda x: x.touched)[:-8]:
                    self.replays.pop(old.gid, None)
        return r


APP = App()


def to_json(obj):
    return json.dumps(obj, separators=(",", ":")).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "HexfieldWeb/2.0"

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    # ------------------------------------------------------------ plumbing
    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=()):
        data = body if isinstance(body, bytes) else to_json(body)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self, limit):
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 <= n <= limit:
            raise ValueError("bad Content-Length")
        if not n:
            return {}
        # a JSON content type can't be sent cross-site without a preflight, which we never
        # answer: other web pages can't post to this server behind your back
        if "json" not in (self.headers.get("Content-Type") or ""):
            raise ValueError("send JSON")
        body = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("body must be a JSON object")
        return body

    def local(self):
        return self.client_address[0] in LOCAL

    def do_GET(self):
        url = urlparse(self.path)
        if url.path.startswith("/api/"):
            return self._api("GET", url.path[5:], parse_qs(url.query), {})
        path = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC, path))
        if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_POST(self):
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        route = url.path[5:]
        try:
            body = self._body(MAX_IMPORT if route == "import" else MAX_BODY)
        except ValueError as ex:
            return self._send(400, {"error": f"bad request body: {ex}"})
        self._api("POST", route, parse_qs(url.query), body)

    # ------------------------------------------------------------ routes
    def _api(self, method, route, query, body):
        def q(key, default=None):
            return query.get(key, [default])[0]

        try:
            post = method == "POST"
            try:
                since = max(0, int(q("since", "0")))
            except ValueError:
                raise IllegalAction("since must be a number")
            if route in HOST_ONLY and not self.local():
                raise Forbidden("only the host can do that")
            if route in HOST_ONLY and route != "new":
                return self._host(route, post, q, body)
            new = route == "new" and post
            g = APP.new(body) if new else APP.get(q("g"), disk=self.local())
            if g is None:
                return self._send(200, {"nogame": True})
            if route == "wait":  # long poll: answer when the game has moved on
                try:
                    g.wait(int(q("v", "-1")))
                except ValueError:
                    raise IllegalAction("v must be a number")
            with g.lock:
                extra = {}
                if route == "join" and post:
                    seat, token = g.join(body.get("name"))
                    extra["tokens"] = {str(seat): token}
                else:
                    seat = g.seat_of(q("t"))
                    if seat is None and q("t"):
                        raise Forbidden("that seat key doesn't belong to this game")
                    if new:
                        seat = g.humans[0]
                    if seat is None:
                        # an online game seats people by their key alone (the host gets theirs
                        # back by resuming the game from the saved-games list)
                        if not self.local() or g.mode == "online":
                            return self._send(200, self._lobby(g))
                        seat = g.humans[0]  # this machine, no key: the host's own seat
                if new:
                    extra["board"] = g.geometry
                    extra["tokens"] = g.tokens_of(g.humans if g.mode != "online" else g.humans[:1])
                elif route == "board":
                    extra["board"] = g.geometry
                elif route in ("state", "wait", "join"):
                    if route == "join":
                        extra["board"] = g.geometry
                elif route == "step" and post:
                    g.step(body.get("v") if isinstance(body.get("v"), int) else None)
                elif route == "act" and post:
                    g.act(body.get("action"), seat)
                elif route == "respond" and post:
                    g.respond(bool(body.get("accept")), seat, body.get("counter"))
                elif route == "offer" and post:
                    g.make_offer(body.get("give", []), body.get("get", []), seat)
                elif route == "undo" and post:
                    extra["undone"] = g.undo(seat)
                    since = 0
                    extra["reset_log"] = True
                elif route == "start" and post:
                    if seat != g.humans[0]:
                        raise Forbidden("only the host can start the game")
                    g.fill_with_bots()
                elif route == "hint":
                    extra["hint"] = g.hint(seat)
                elif route == "eval":
                    extra["eval"] = g.evaluate(seat)
                elif route == "settings" and post:
                    if "review" in body:
                        g.review_on[seat] = bool(body["review"])
                else:
                    return self._send(404, {"error": "unknown route"})
                # serialise under the lock: view() holds live lists the bots/reviewer mutate
                if APP.lan:
                    extra["lan"] = APP.lan
                data = to_json({**g.view(seat, since), **extra})
            return self._send(200, data)
        except IllegalAction as ex:
            return self._send(400, {"error": str(ex)})
        except Forbidden as ex:
            return self._send(403, {"error": str(ex)})
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as ex:
            traceback.print_exc()
            return self._send(500, {"error": f"{type(ex).__name__}: {ex}"})

    def _lobby(self, g):
        """What someone without a seat may know: who is playing and whether a seat is free."""
        return {"gid": g.gid, "join": True, "full": not g.open, "mode": g.mode,
                "players": [{"name": g.s.names[p], "bot": g.is_bot(p), "open": p in g.open} for p in range(g.s.n)]}

    def _host(self, route, post, q, body):
        """Saved games, replays and statistics: this machine only."""
        gid = body.get("id") or q("id")
        if route == "games":
            live = {g.gid for g in APP.games.values()}
            games = store.list_games()
            for x in games:
                x["live"] = x["id"] in live
            return self._send(200, {"games": games})
        if route == "stats":
            return self._send(200, store.stats())
        try:
            if route == "delete" and post:
                g = APP.games.pop(gid, None)
                if g is not None:
                    g.persist = False
                    g.close()
                    if APP.game is g:
                        APP.game = None
                APP.replays.pop(gid, None)
                return self._send(200, {"deleted": store.delete(gid)})
            if route == "import" and post:
                rec = Record.from_dict(body.get("record"))
                rec.replay()  # refuses a file that doesn't play back
                gid = store.new_id()
                store.save(gid, rec)
                return self._send(200, {"id": gid})
            if route == "export":
                rec, _ = store.load(gid)
                return self._send(200, rec.dumps().encode(), headers=[
                    ("Content-Disposition", f'attachment; filename="hexfield-{gid}.json"')])
            if route in ("resume", "branch") and post:
                if route == "branch":
                    entry = body.get("entry")
                    if not isinstance(entry, int) or entry < 0:
                        raise IllegalAction("entry must be a number")
                    g = APP.add(WebGame.resume(gid, upto=entry))
                    g.save()
                else:
                    g = APP.get(gid)
                    if g is None:
                        raise IllegalAction("no such saved game")
                    with APP.lock:
                        APP.game = g
                with g.lock:
                    seat = g.humans[0]
                    return self._send(200, {**g.view(seat, 0), "board": g.geometry,
                                            "tokens": g.tokens_of(g.humans)})
            if route in ("replay", "replay_hint"):
                r = APP.replay(gid)
                with r.lock:
                    try:
                        k = int(q("k", "0"))
                        seat = int(q("seat", str(r.human)))
                    except ValueError:
                        raise IllegalAction("k and seat must be numbers")
                    if seat not in range(r.s.n):
                        raise IllegalAction("no such seat")
                    r.seek(k)
                    if route == "replay_hint":
                        return self._send(200, {"hint": r.hint_here(seat)})
                    return self._send(200, {**r.view(seat, 0, reveal=q("reveal") == "1"), "board": r.geometry})
        except (OSError, ValueError, KeyError) as ex:
            raise IllegalAction(f"can't open that game: {ex}")
        return self._send(404, {"error": "unknown route"})


def lan_address():
    """This machine's address on the local network (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hexfield.web", description="Hexfield in the browser")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--lan", action="store_true",
                    help="listen on every interface so other devices on your network can join online games")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    ap.add_argument("--no-save", action="store_true", help="don't write games to disk")
    args = ap.parse_args(argv)
    APP.persist = not args.no_save
    if APP.persist:
        store.prune()
    host = "0.0.0.0" if args.lan else args.host
    httpd = ThreadingHTTPServer((host, args.port), Handler)
    httpd.daemon_threads = True
    url = f"http://{'127.0.0.1' if args.lan else args.host}:{args.port}/"
    print(f"  Hexfield web UI running at {url}  (Ctrl+C to stop)")
    if args.lan:
        addr = APP.lan = lan_address()
        print(f"  Others on your network can join an online game at http://{addr or '<this machine>'}:{args.port}/"
              "  (they need the invite link from the game)")
    if APP.persist:
        print(f"  Games are saved in {store.games_dir()}")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  bye")
    finally:
        httpd.server_close()
        APP.close()


if __name__ == "__main__":
    main()
