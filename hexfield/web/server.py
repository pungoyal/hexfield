"""Local web server for the browser UI (standard library only).

    python3 -m hexfield.web              # opens http://127.0.0.1:8765
    python3 -m hexfield.web --port 9000 --no-browser
"""

import argparse
import json
import mimetypes
import os
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..game import IllegalAction
from .session import WebGame

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class App:
    """Holds the single game this server is hosting."""

    def __init__(self):
        self.game = None
        self.lock = threading.Lock()

    def new(self, body):
        opp = int(body.get("opponents", 3))
        if opp not in (2, 3):
            raise IllegalAction("opponents must be 2 or 3")
        level = body.get("level", "hard")
        if level not in ("easy", "medium", "hard"):
            raise IllegalAction("unknown level")
        seed = body.get("seed")
        seed = int(seed) if seed not in (None, "") else None
        seat = body.get("seat")
        seat = int(seat) if seat not in (None, "", 0, "0") else None
        if seat is not None and not 1 <= seat <= opp + 1:
            raise IllegalAction("seat out of range")
        name = (str(body.get("name") or "You").strip() or "You")[:14]
        with self.lock:
            self.game = WebGame(opp, level, seed, seat, name, review=bool(body.get("review", True)))
        return self.game


APP = App()


class Handler(BaseHTTPRequestHandler):
    server_version = "HexfieldWeb/1.0"

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    # ------------------------------------------------------------ plumbing
    def _send(self, code, body, ctype="application/json; charset=utf-8", cache=False):
        data = body if isinstance(body, bytes) else json.dumps(body, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n) or b"{}")

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
        try:
            body = self._body()
        except ValueError:
            return self._send(400, {"error": "bad json"})
        self._api("POST", url.path[5:], parse_qs(url.query), body)

    # ------------------------------------------------------------ routes
    def _api(self, method, route, query, body):
        try:
            if route == "new" and method == "POST":
                g = APP.new(body)
                with g.lock:
                    out = g.view()
                    out["board"] = g.geometry
                return self._send(200, out)
            g = APP.game
            if g is None:
                return self._send(200, {"nogame": True})
            since = int(query.get("since", ["0"])[0])
            with g.lock:
                extra = {}
                if route == "state":
                    pass
                elif route == "board":
                    extra["board"] = g.geometry
                elif route == "step" and method == "POST":
                    extra["stepped"] = g.step()
                elif route == "act" and method == "POST":
                    g.act(body.get("action"))
                elif route == "respond" and method == "POST":
                    g.respond(bool(body.get("accept")))
                elif route == "offer" and method == "POST":
                    g.offer(body.get("give", []), body.get("get", []))
                elif route == "undo" and method == "POST":
                    g.undo()
                    since = 0
                    extra["reset_log"] = True
                elif route == "hint":
                    extra["hint"] = g.hint()
                elif route == "eval":
                    extra["eval"] = g.evaluate()
                elif route == "settings" and method == "POST":
                    if "review" in body:
                        g.review_on = bool(body["review"])
                else:
                    return self._send(404, {"error": "unknown route"})
                out = g.view(since)
                out.update(extra)
            return self._send(200, out)
        except IllegalAction as ex:
            return self._send(400, {"error": str(ex)})
        except Exception as ex:
            traceback.print_exc()
            return self._send(500, {"error": f"{type(ex).__name__}: {ex}"})


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hexfield.web", description="Hexfield in the browser")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = ap.parse_args(argv)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"  Hexfield web UI running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  bye")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
