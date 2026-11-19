"""Local web server for the browser UI (standard library only).

    python3 -m hexfield.web              # opens http://127.0.0.1:8765
    python3 -m hexfield.web --port 9000 --no-browser
    python3 -m hexfield.web --lan        # let other devices on your network join online games
    HEXFIELD_HOST_KEY=... python3 -m hexfield.web --lan --no-browser --allow-host play.example.com

The server can run several games at once. A browser names its game with ?g=<id> and
proves its seat with an `Authorization: Bearer <seat key>` header; without one a request
from this machine acts for the first human seat, so a single-player game needs no
ceremony. Starting, resuming, listing and deleting games is only allowed from this
machine: someone who joins over the network can play in the game they were invited to
and nothing else.

When the server runs where you can't sit at it (a container, a small VM behind a reverse
proxy), set a host key. Opening /#host=<key> once makes that browser the host, and
requests from the machine itself stop being special. Neither key is ever in a URL the
server sees, so a proxy's access log can't record one: the fragment stays in the browser,
which posts the host key to /api/host, and proxies redact Authorization. See "Hosting" in
the README for the whole picture.
"""

import argparse
import atexit
import gzip
import hashlib
import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import signal
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from .. import __version__, store
from ..game import IllegalAction
from ..record import Record
from .session import MODES, Replay, WebGame

MAX_BODY = 64 * 1024
MAX_IMPORT = 4 * 1024 * 1024
MAX_LIVE = 24           # live games kept in memory; the rest wait on disk
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
LOCAL = ("127.0.0.1", "::1", "::ffff:127.0.0.1")
MAX_CONN = 200          # requests in flight; more are turned away instead of piling up threads
RATE, BURST = 100.0, 300.0   # requests a second one remote address may make, and its allowance
MIN_HOST_KEY = 16
COOKIE = "hexfield_host"
PROXY_HEADERS = ("X-Forwarded-For", "Forwarded", "X-Real-IP")
# the client is plain files from this server: nothing else may run, frame it or be sent a
# Referer (game ids travel in the query string)
SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"),
    ("Content-Security-Policy",
     "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
     "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
)
log = logging.getLogger("hexfield.web")
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
        self.host_key = None  # sha256 of the host key; when set, it is the only way to be the host
        self.hosts = {"localhost", socket.gethostname().lower()}  # names this server answers to
        self.buckets = {}   # remote address -> [allowance, last seen]
        atexit.register(self.close)

    def set_host_key(self, key):
        self.host_key = hashlib.sha256(key.encode()).hexdigest() if key else None

    def allow(self, addr, now=None):
        """Spend one request from this address's allowance; False when it has run out."""
        now = time.monotonic() if now is None else now
        with self.lock:
            if len(self.buckets) > 4096:
                self.buckets = {a: b for a, b in self.buckets.items() if now - b[1] < 60}
            b = self.buckets.setdefault(addr, [BURST, now])
            b[0] = min(BURST, b[0] + (now - b[1]) * RATE) - 1
            b[1] = now
            if b[0] < 0:
                b[0] = 0
                return False
            return True

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


_static = {}  # path -> (stamp, etag, raw bytes, gzipped bytes or None)


def static_file(full):
    """A static file with its ETag and gzipped form, re-read only when it changes on disk."""
    st = os.stat(full)
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _static.get(full)
    if hit is None or hit[0] != stamp:
        with open(full, "rb") as f:
            raw = f.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        text = ctype.startswith("text/") or ctype.endswith(("javascript", "json", "xml"))
        gz = gzip.compress(raw, 6, mtime=0) if text and len(raw) > 1024 else None
        hit = _static[full] = (stamp, '"' + hashlib.sha256(raw).hexdigest()[:24] + '"', raw, gz,
                               ctype + ("; charset=utf-8" if text else ""))
    return hit[1:]


def host_name(value):
    """The name part of a Host header or an Origin: 'Example.com:8765' -> 'example.com'."""
    value = (value or "").strip().lower()
    if value.startswith("["):
        return value[1:value.find("]")] if "]" in value else ""
    return value.rsplit(":", 1)[0] if ":" in value else value


class Handler(BaseHTTPRequestHandler):
    server_version = "Hexfield"
    sys_version = ""
    timeout = 30  # a client that stops sending mid-request doesn't hold a thread for ever

    def log_message(self, fmt, *args):  # http.server's own complaints (bad request lines, timeouts)
        log.debug("%s %s", self.client_address[0], fmt % args)

    def log_request(self, code="-", size="-"):  # the access log (--verbose); keys are never in a URL
        if log.isEnabledFor(logging.INFO):
            log.info("%s %s %s %s", self.remote(), self.command, self.path, code)

    # ------------------------------------------------------------ plumbing
    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=(), cache="no-store"):
        data = body if isinstance(body, bytes) else to_json(body)
        self.send_response(code)
        if code not in (304,):
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        for k, v in SECURITY_HEADERS:
            self.send_header(k, v)
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD" and code != 304:
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

    def proxied(self):
        return any(self.headers.get(h) for h in PROXY_HEADERS)

    def remote(self):
        """Who is asking: the peer, or the client a reverse proxy on this machine names."""
        peer = self.client_address[0]
        fwd = self.headers.get("X-Forwarded-For") or self.headers.get("X-Real-IP")
        if peer in LOCAL and fwd:
            return fwd.split(",")[-1].strip()[:64] or peer
        return peer

    def seat_key(self):
        """The seat key from `Authorization: Bearer <key>`, or None. A header, not the query
        string, so it never reaches an access log or a browser's history."""
        scheme, _, key = (self.headers.get("Authorization") or "").partition(" ")
        return (key.strip() or None) if scheme.lower() == "bearer" else None

    def has_host_key(self):
        if not APP.host_key:
            return False
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE and hmac.compare_digest(v.encode(), APP.host_key.encode()):
                return True
        return False

    def local(self):
        """Is this the host? With a host key, whoever holds it. Otherwise a request made on
        this machine itself: one a reverse proxy passed on came from somewhere else."""
        if APP.host_key:
            return self.has_host_key()
        return self.client_address[0] in LOCAL and not self.proxied()

    def _guard(self):
        """Checks every request passes first. False when it has been answered already."""
        # A web page can point its own domain at 127.0.0.1 and then talk to this server as
        # "same origin" (DNS rebinding). Its requests still carry that domain in Host, so
        # only answer to an address or to a name we know.
        name = host_name(self.headers.get("Host"))
        if name and not self._known(name):
            self._send(403, {"error": f"this server doesn't answer to {name!r}: start it with --allow-host {name}"})
            return False
        origin = self.headers.get("Origin")
        if self.command == "POST" and origin is not None:
            o = host_name(urlparse(origin).netloc)
            if not o or (o != name and o not in APP.hosts):
                self._send(403, {"error": "cross-site request refused"})
                return False
        if (self.client_address[0] not in LOCAL or self.proxied()) and not APP.allow(self.remote()):
            self._send(429, {"error": "too many requests, slow down"}, headers=[("Retry-After", "1")])
            return False
        return True

    @staticmethod
    def _known(name):
        try:
            ipaddress.ip_address(name)
            return True  # an address can't be rebound
        except ValueError:
            return name in APP.hosts or name.endswith(".local")

    def _login(self, body):
        """POST /api/host {"key": ...}: make this browser the host. The page sends it from
        /#host=<key>, whose fragment no server or proxy ever sees."""
        key = body.get("key")
        if not APP.host_key or not isinstance(key, str) or not hmac.compare_digest(
                hashlib.sha256(key.encode()).hexdigest().encode(), APP.host_key.encode()):
            return self._send(403, {"error": "wrong host key"})
        secure = "; Secure" if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https" else ""
        return self._send(200, {"ok": True}, headers=[
            ("Set-Cookie", f"{COOKIE}={APP.host_key}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Strict{secure}")])

    def do_GET(self):
        if not self._guard():
            return None
        url = urlparse(self.path)
        if url.path == "/healthz":
            return self._send(200, {"ok": True, "version": __version__})
        if url.path.startswith("/api/"):
            if self.command == "HEAD":
                return self._send(405, {"error": "use GET"}, headers=[("Allow", "GET, POST")])
            return self._api("GET", url.path[5:], parse_qs(url.query), {})
        path = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC, path))
        if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        etag, raw, gz, ctype = static_file(full)
        # always revalidate (a new version shows at once), but an unchanged file costs no bytes
        headers = [("ETag", etag), ("Vary", "Accept-Encoding")]
        if etag in (self.headers.get("If-None-Match") or ""):
            return self._send(304, b"", headers=headers, cache="no-cache")
        if gz is not None and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            return self._send(200, gz, ctype, headers + [("Content-Encoding", "gzip")], cache="no-cache")
        return self._send(200, raw, ctype, headers, cache="no-cache")

    do_HEAD = do_GET

    def do_POST(self):
        if not self._guard():
            return None
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        route = url.path[5:]
        try:
            body = self._body(MAX_IMPORT if route == "import" else MAX_BODY)
        except ValueError as ex:
            return self._send(400, {"error": f"bad request body: {ex}"})
        if route == "host":
            return self._login(body)
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
            if new:
                g = APP.new(body)
            else:
                # "the newest game" is the host's shortcut; anyone else must name the game,
                # which only an invite link tells them
                g = APP.get(q("g"), disk=self.local()) if q("g") or self.local() else None
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
                    key = self.seat_key()
                    seat = g.seat_of(key)
                    if seat is None and key:
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
            log.exception("%s /api/%s failed", method, route)
            # the details are for whoever runs the server, not for whoever sent the request
            return self._send(500, {"error": f"{type(ex).__name__}: {ex}" if self.local() else "internal error"})

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


class Server(ThreadingHTTPServer):
    """One thread per request, but only so many at once."""
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.slots = threading.BoundedSemaphore(MAX_CONN)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            return self.shutdown_request(request)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):  # a dropped connection is not news
        log.debug("request from %s failed", client_address[0], exc_info=True)


def _stop(*_):
    raise KeyboardInterrupt


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hexfield.web", description="Hexfield in the browser")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--lan", action="store_true",
                    help="listen on every interface so other devices on your network can join online games")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    ap.add_argument("--no-save", action="store_true", help="don't write games to disk")
    ap.add_argument("--host-key", default=os.environ.get("HEXFIELD_HOST_KEY"), metavar="KEY",
                    help="for a server you can't sit at: whoever opens /#host=KEY is the host, and requests "
                         "from this machine no longer are (or set HEXFIELD_HOST_KEY)")
    ap.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                    help="a domain name this server is reached by (repeatable); addresses, localhost "
                         "and .local names always work")
    ap.add_argument("-v", "--verbose", action="store_true", help="log every request")
    ap.add_argument("--version", action="version", version=f"hexfield {__version__}")
    args = ap.parse_args(argv)
    if args.host_key and len(args.host_key) < MIN_HOST_KEY:
        ap.error(f"the host key must be at least {MIN_HOST_KEY} characters; make one with:  "
                 'python3 -c "import secrets; print(secrets.token_urlsafe(24))"')
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    host = "0.0.0.0" if args.lan else args.host
    try:
        httpd = Server((host, args.port), Handler)
    except OSError as ex:
        ap.exit(1, f"hexfield.web: can't listen on {host}:{args.port}: {ex.strerror or ex}\n"
                   "  (is another copy running? try --port)\n")
    APP.persist = not args.no_save
    APP.set_host_key(args.host_key)
    APP.hosts.update(h.lower() for h in args.allow_host)
    if APP.persist:
        store.prune()
    try:
        signal.signal(signal.SIGTERM, _stop)  # `docker stop` and service managers end it like Ctrl+C
    except ValueError:
        pass  # not the main thread (embedded in something else)
    url = f"http://{'127.0.0.1' if args.lan else args.host}:{args.port}/"
    print(f"  Hexfield web UI running at {url}  (Ctrl+C to stop)")
    if args.lan:
        addr = APP.lan = lan_address()
        print(f"  Others on your network can join an online game at http://{addr or '<this machine>'}:{args.port}/"
              "  (they need the invite link from the game)")
    if args.host_key:
        url += "#" + urlencode({"host": args.host_key})
        print("  Host key set: open  /#host=<your key>  once in the browser that runs the games")
    if APP.persist:
        print(f"  Games are saved in {store.games_dir()}", flush=True)
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
