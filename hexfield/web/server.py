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

With --accounts (a public server) anyone can make an account with a passkey and join the
games they are invited to. Hosting, that is starting games and keeping saved games, is
for the accounts the admin allows: the admin is whoever holds the host key. Each account
sees only its own saved games.
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
import secrets
import signal
import socket
import sqlite3
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from .. import __version__, store
from ..game import IllegalAction
from ..record import Record
from . import passkey
from .accounts import Accounts
from .session import MODES, Replay, WebGame

MAX_BODY = 64 * 1024
MAX_IMPORT = 4 * 1024 * 1024
MAX_LIVE = 24           # live games kept in memory; the rest wait on disk
MAX_LIVE_EACH = 4       # of them, an account's own: one host can't push everyone else's out
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
LOCAL = ("127.0.0.1", "::1", "::ffff:127.0.0.1")
MAX_CONN = 200          # requests in flight; more are turned away instead of piling up threads
RATE, BURST = 100.0, 300.0   # requests a second one remote address may make, and its allowance
MIN_HOST_KEY = 16
COOKIE = "hexfield_host"
SESSION = "hexfield_session"
USER_GAMES = 50         # saved games an account keeps; the oldest finished ones go first
MAX_USERS = 5000
SIGNUP_RATE, SIGNUP_BURST = 5 / 3600, 5.0   # new accounts one remote address may make: 5 an hour
CHALLENGE_TTL = 300     # seconds a passkey prompt stays good for
MAX_CHALLENGES = 10000
MAX_CHALLENGES_EACH = 20  # open passkey prompts one remote address may hold
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
ACCOUNT_ROUTES = {"me", "signup-options", "signup", "signin-options", "signin", "signout", "accounts"}


class Forbidden(Exception):
    pass


class App:
    """The games this server is hosting."""

    def __init__(self):
        self.games = {}     # gid -> WebGame
        self.replays = {}   # (owner, gid) -> Replay
        self.latest = {}    # owner -> their newest game: what a request without ?g= means
        self.lock = threading.Lock()
        self.persist = True
        self.lan = None     # this machine's network address when others can reach the server
        self.host_key = None  # sha256 of the host key; when set, it is the only way to be the host
        self.hosts = {"localhost", socket.gethostname().lower()}  # names this server answers to
        self.buckets = {}   # remote address -> [allowance, last seen]
        self.behind_proxy = False  # every request comes through a reverse proxy: trust its X-Forwarded-For
        self.accounts = None       # Accounts, on a server with --accounts
        self.challenges = {}       # passkey challenge -> (what for, expiry, details, remote address)
        atexit.register(self.close)

    @property
    def game(self):
        """The newest game of whoever runs the server."""
        return self.latest.get("")

    def set_host_key(self, key):
        self.host_key = hashlib.sha256(key.encode()).hexdigest() if key else None

    def allow(self, addr, now=None, rate=RATE, burst=BURST):
        """Spend one request from this address's allowance; False when it has run out."""
        now = time.monotonic() if now is None else now
        with self.lock:
            if len(self.buckets) > 4096:
                # an idle allowance is full again after a minute; a sign-up allowance after an hour
                self.buckets = {a: b for a, b in self.buckets.items() if now - b[1] < (3600 if isinstance(a, tuple) else 60)}
            b = self.buckets.setdefault(addr, [burst, now])
            b[0] = min(burst, b[0] + (now - b[1]) * rate) - 1
            b[1] = now
            if b[0] < 0:
                b[0] = 0
                return False
            return True

    def challenge(self, kind, addr, **details):
        """A fresh passkey challenge, good for one answer."""
        now = time.monotonic()
        with self.lock:
            self.challenges = {c: v for c, v in self.challenges.items() if v[1] > now}
            if len(self.challenges) >= MAX_CHALLENGES or \
                    sum(v[3] == addr for v in self.challenges.values()) >= MAX_CHALLENGES_EACH:
                raise Forbidden("too many sign-ins at once, try again in a few minutes")
            c = passkey.b64u(secrets.token_bytes(32))
            self.challenges[c] = (kind, now + CHALLENGE_TTL, details, addr)
        return c

    def answer(self, kind, challenge):
        """The details of a challenge this server gave out and nobody has answered yet."""
        with self.lock:
            got = self.challenges.pop(challenge, None)
        if got is None or got[0] != kind or got[1] < time.monotonic():
            raise Forbidden("that passkey prompt has expired, try again")
        return got[2]

    def close(self):
        for g in list(self.games.values()):
            g.close()

    def add(self, game):
        with self.lock:
            self.games[game.gid] = game
            self.latest[game.owner] = game
            idle = sorted(self.games.values(), key=lambda g: g.touched)
            mine = [g for g in idle if g.owner == game.owner] if game.owner else []
            drop = mine[:max(0, len(mine) - MAX_LIVE_EACH)]
            idle = [g for g in idle if g not in drop]
            drop += idle[:max(0, len(idle) - MAX_LIVE)]
            for g in drop:
                del self.games[g.gid]
                if self.latest.get(g.owner) is g:
                    del self.latest[g.owner]
        for g in drop:
            g.close()
        return game

    def get(self, gid, owner=None):
        """A live game by id. For an owner (see Handler.owner), their newest game when no
        id is given, and one of their games that is only on disk, brought back first."""
        if not gid:
            return self.latest.get(owner) if owner is not None else None
        g = self.games.get(gid)
        if g is None and self.persist and owner is not None:
            try:
                g = WebGame.resume(gid, owner=owner)
            except (OSError, ValueError, KeyError):
                return None
            self.add(g)
        return g

    def forget(self, owner):
        """A deleted account: its live games and its saved ones go."""
        with self.lock:
            gone = [g for g in self.games.values() if g.owner == owner]
            for g in gone:
                del self.games[g.gid]
            self.latest.pop(owner, None)
            self.replays = {k: r for k, r in self.replays.items() if k[0] != owner}
        for g in gone:
            g.persist = False
            g.close()
        store.delete_owner(owner)

    def own(self, gid, owner):
        """One of this owner's games, or None."""
        g = self.get(gid, owner)
        return g if g is not None and g.owner == owner else None

    def new(self, body, owner=""):
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
                       humans=humans, mode=mode, persist=self.persist, owner=owner)
        if owner and self.persist:
            store.prune(USER_GAMES, owner)
        if mode == "online":
            game.save()  # the seat keys must survive a restart; other games are saved at the first move
        return self.add(game)

    def replay(self, gid, owner=""):
        r = self.replays.get((owner, gid))
        live = self.games.get(gid)
        if live is not None and live.owner != owner:
            live = None
        if live is not None:
            live.save()
        if r is None or (live is not None and len(live.rec.entries) != len(r.rec.entries)):
            r = Replay(gid, owner=owner)
            with self.lock:
                self.replays[(owner, gid)] = r
                for key, old in sorted(self.replays.items(), key=lambda x: x[1].touched)[:-8]:
                    self.replays.pop(key, None)
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
        """Who is asking: the peer, or the client a reverse proxy names (one on this machine,
        or with --behind-proxy, the one every request comes through)."""
        peer = self.client_address[0]
        fwd = self.headers.get("X-Forwarded-For") or self.headers.get("X-Real-IP")
        if fwd and (peer in LOCAL or APP.behind_proxy):
            return fwd.split(",")[-1].strip()[:64] or peer
        return peer

    def client(self):
        """remote(), with an IPv6 address cut to its /64: one network, which is what one
        person can have many addresses in."""
        addr = self.remote()
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return addr
        return str(ipaddress.ip_network(f"{ip}/64", strict=False)) if ip.version == 6 else addr

    def seat_key(self):
        """The seat key from `Authorization: Bearer <key>`, or None. A header, not the query
        string, so it never reaches an access log or a browser's history."""
        scheme, _, key = (self.headers.get("Authorization") or "").partition(" ")
        return (key.strip() or None) if scheme.lower() == "bearer" else None

    def cookie(self, name):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def has_host_key(self):
        return bool(APP.host_key) and hmac.compare_digest((self.cookie(COOKIE) or "").encode(), APP.host_key.encode())

    def account(self):
        """The signed-in account ({"id", "name", "host"}), or None."""
        if APP.accounts is None:
            return None
        if not hasattr(self, "_account"):
            self._account = APP.accounts.user(self.cookie(SESSION))
        return self._account

    def owner(self):
        """Whose games this request may host: "" for the host (see local()), an account's id
        when the admin lets it host, None for anyone else."""
        if self.local():
            return ""
        u = self.account()
        return u["id"] if u and u["host"] else None

    def local(self):
        """Is this the host? With a host key, whoever holds it. Otherwise a request made on
        this machine itself: one a reverse proxy passed on came from somewhere else."""
        if APP.host_key:
            return self.has_host_key()
        return self.client_address[0] in LOCAL and not self.proxied()

    def _guard(self):
        """Checks every request passes first. False when it has been answered already."""
        self.__dict__.pop("_account", None)  # worked out afresh for every request
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

    def https(self):
        return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"

    def _cookie_header(self, name, value, days):
        secure = "; Secure" if self.https() else ""
        return ("Set-Cookie", f"{name}={value}; Path=/; Max-Age={days * 86400}; HttpOnly; SameSite=Strict{secure}")

    def _login(self, body):
        """POST /api/host {"key": ...}: make this browser the host. The page sends it from
        /#host=<key>, whose fragment no server or proxy ever sees."""
        key = body.get("key")
        if not APP.host_key or not isinstance(key, str) or not hmac.compare_digest(
                hashlib.sha256(key.encode()).hexdigest().encode(), APP.host_key.encode()):
            return self._send(403, {"error": "wrong host key"})
        return self._send(200, {"ok": True}, headers=[self._cookie_header(COOKIE, APP.host_key, 30)])

    def _me(self):
        u = self.account()
        return {"accounts": APP.accounts is not None, "admin": APP.accounts is not None and self.local(), "lan": APP.lan,
                "host": self.owner() is not None, "user": u and {"name": u["name"], "host": u["host"]}}

    def _auth(self, route, body):
        """Accounts: passkey sign-up and sign-in, signing out, and the admin's list."""
        if route == "me":
            return self._send(200, self._me())
        acc = APP.accounts
        if acc is None:
            return self._send(404, {"error": "this server has no accounts"})
        rp = host_name(self.headers.get("Host"))
        origin = f"{'https' if self.https() else 'http'}://{self.headers.get('Host')}"
        try:
            if route == "accounts":
                if not self.local():
                    raise Forbidden("only the admin can do that")
                if self.command == "POST":
                    uid = body.get("id")
                    if body.get("delete"):
                        if not acc.delete(uid):
                            raise IllegalAction("no such account")
                        APP.forget(uid)
                    elif not acc.set_host(uid, body.get("host")):
                        raise IllegalAction("no such account")
                return self._send(200, {"users": acc.users()})
            if self.command != "POST":
                return self._send(405, {"error": "use POST"}, headers=[("Allow", "POST")])
            if route == "signup-options":
                name = " ".join(str(body.get("name") or "").split())[:14]  # a player's name: see clean_name
                if not name:
                    raise IllegalAction("choose a name")
                if acc.named(name):
                    raise IllegalAction(f"someone is already called {name}")
                if acc.count() >= MAX_USERS:
                    raise Forbidden("this server isn't taking new accounts")
                handle = secrets.token_bytes(16)
                return self._send(200, {
                    "challenge": APP.challenge("signup", self.client(), name=name, id=handle.hex()),
                    "rp": {"id": rp, "name": "Hexfield"},
                    "user": {"id": passkey.b64u(handle), "name": name, "displayName": name},
                    "pubKeyCredParams": [{"type": "public-key", "alg": passkey.ES256}],
                    "authenticatorSelection": {"residentKey": "required", "userVerification": "preferred"},
                    "attestation": "none", "timeout": CHALLENGE_TTL * 1000})
            if route == "signin-options":
                return self._send(200, {"challenge": APP.challenge("signin", self.client()), "rpId": rp,
                                        "userVerification": "preferred", "timeout": CHALLENGE_TTL * 1000})
            if route == "signout":
                token = self.cookie(SESSION)
                if token:
                    acc.end_session(token)
                return self._send(200, {"ok": True}, headers=[self._cookie_header(SESSION, "", 0)])
            client_data = passkey.unb64u(body.get("clientData", ""))
            auth_data = passkey.unb64u(body.get("authData", ""))
            if route == "signup":
                challenge, cred, key, count = passkey.registration(
                    client_data, auth_data, passkey.unb64u(body.get("publicKey", "")), body.get("alg"), rp, origin)
                want = APP.answer("signup", challenge)
                # spent only by a sign-up that would succeed, so a failed prompt costs nothing
                if not APP.allow(("signup", self.client()), rate=SIGNUP_RATE, burst=SIGNUP_BURST):
                    return self._send(429, {"error": "too many new accounts from your address, try again later"})
                try:
                    acc.create(want["id"], want["name"], cred, key, count)
                except sqlite3.IntegrityError:
                    raise IllegalAction("that name or passkey already has an account")
                uid = want["id"]
            elif route == "signin":
                cred = passkey.unb64u(body.get("id", ""))
                known = acc.passkey(cred)
                if known is None:
                    raise Forbidden("this server doesn't know that passkey (was its account deleted?)")
                uid, key, last = known
                challenge, count = passkey.assertion(client_data, auth_data, passkey.unb64u(body.get("signature", "")),
                                                     key, rp, origin)
                APP.answer("signin", challenge)
                if count and count <= last:
                    raise Forbidden("that passkey looks copied; sign in with another")
                acc.used(cred, count)
            else:
                return self._send(404, {"error": "unknown route"})
            return self._send(200, {"ok": True}, headers=[self._cookie_header(SESSION, acc.start_session(uid), 30)])
        except passkey.Invalid as ex:
            return self._send(400, {"error": str(ex)})
        except IllegalAction as ex:
            return self._send(400, {"error": str(ex)})
        except Forbidden as ex:
            return self._send(403, {"error": str(ex)})

    def do_GET(self):
        if not self._guard():
            return None
        url = urlparse(self.path)
        if url.path == "/healthz":
            return self._send(200, {"ok": True, "version": __version__})
        if url.path.startswith("/api/"):
            if self.command == "HEAD":
                return self._send(405, {"error": "use GET"}, headers=[("Allow", "GET, POST")])
            if url.path[5:] in ACCOUNT_ROUTES:
                return self._auth(url.path[5:], {})
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
        if route in ACCOUNT_ROUTES:
            return self._auth(route, body)
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
            owner = self.owner()
            if route in HOST_ONLY and owner is None:
                raise Forbidden("only the host can do that" if APP.accounts is None
                                else "hosting games needs the admin's approval" if self.account()
                                else "sign in to host games")
            if route in HOST_ONLY and route != "new":
                return self._host(route, post, q, body, owner)
            new = route == "new" and post
            if new:
                u = self.account()
                if u and not self.local():  # an account plays under its own name, in the first human seat
                    names = body.get("names")
                    body = {**body, "name": u["name"]}
                    if isinstance(names, list) and names:
                        body["names"] = [u["name"], *names[1:]]
                g = APP.new(body, owner)
            else:
                # "the newest game" is the host's shortcut; anyone else must name the game,
                # which only an invite link tells them
                g = APP.get(q("g"), owner)
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
                    name = body.get("name")
                    if APP.accounts is not None and not self.local():
                        if self.account() is None:
                            raise Forbidden("sign in to join a game")
                        name = self.account()["name"]
                    seat, token = g.join(name)
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
                        if owner != g.owner or g.mode == "online":
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
                    extra["undone"] = g.undo(seat)  # None: the others are being asked
                    if extra["undone"] is not None:
                        since = 0
                        extra["reset_log"] = True
                elif route == "takeback" and post:
                    g.answer_takeback(bool(body.get("ok")), seat)
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
                    if "mute_offers" in body:
                        g.mute_offers(seat, bool(body["mute_offers"]))
                else:
                    return self._send(404, {"error": "unknown route"})
                # after an undo the log a browser holds is stale: send it whole
                if q("ep") is not None and q("ep") != str(g.epoch):
                    since = 0
                    extra["reset_log"] = True
                # serialise under the lock: view() holds live lists the bots/reviewer mutate
                if APP.lan:
                    extra["lan"] = APP.lan
                extra["own"] = owner is not None and owner == g.owner  # its replay is theirs to watch
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

    def _host(self, route, post, q, body, owner):
        """Saved games, replays and statistics: the host's own (an account's own)."""
        gid = body.get("id") or q("id")
        if route == "games":
            live = {g.gid for g in APP.games.values() if g.owner == owner}
            games = store.list_games(owner=owner)
            for x in games:
                x["live"] = x["id"] in live
            return self._send(200, {"games": games, "other_versions": store.other_versions(owner)})
        if route == "stats":
            return self._send(200, store.stats(owner=owner))
        try:
            if route == "delete" and post:
                with APP.lock:
                    g = APP.games.get(gid)
                    if g is not None and g.owner == owner:
                        del APP.games[gid]
                        if APP.latest.get(owner) is g:
                            del APP.latest[owner]
                    else:
                        g = None
                    APP.replays.pop((owner, gid), None)
                if g is not None:
                    g.persist = False
                    g.close()
                return self._send(200, {"deleted": store.delete(gid, owner)})
            if route == "import" and post:
                rec = Record.from_dict(body.get("record"))
                rec.replay()  # refuses a file that doesn't play back
                gid = store.new_id()
                store.save(gid, rec, owner)
                if owner:
                    store.prune(USER_GAMES, owner)
                return self._send(200, {"id": gid})
            if route == "export":
                rec, _ = store.load(gid, owner)
                return self._send(200, rec.dumps().encode(), headers=[
                    ("Content-Disposition", f'attachment; filename="hexfield-{gid}.json"')])
            if route in ("resume", "branch") and post:
                if route == "branch":
                    entry = body.get("entry")
                    if not isinstance(entry, int) or entry < 0:
                        raise IllegalAction("entry must be a number")
                    g = APP.add(WebGame.resume(gid, upto=entry, owner=owner))
                    g.save()
                else:
                    g = APP.own(gid, owner)
                    if g is None:
                        raise IllegalAction("no such saved game")
                    with APP.lock:
                        APP.latest[owner] = g
                with g.lock:
                    seat = g.humans[0]
                    return self._send(200, {**g.view(seat, 0), "board": g.geometry,
                                            "tokens": g.tokens_of(g.humans)})
            if route in ("replay", "replay_hint"):
                r = APP.replay(gid, owner)
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
    ap.add_argument("--accounts", action="store_true",
                    help="a public server: anyone can make an account with a passkey and join games; the "
                         "admin (whoever holds the host key) chooses who may host. Needs --host-key and HTTPS")
    ap.add_argument("--behind-proxy", action="store_true",
                    help="every request comes through a reverse proxy, and nothing else can reach the server "
                         "(any peer's X-Forwarded-For is believed): rate limits go by the client address in it")
    ap.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                    help="a domain name this server is reached by (repeatable); addresses, localhost "
                         "and .local names always work")
    ap.add_argument("-v", "--verbose", action="store_true", help="log every request")
    ap.add_argument("--version", action="version", version=f"hexfield {__version__}")
    args = ap.parse_args(argv)
    if args.host_key and len(args.host_key) < MIN_HOST_KEY:
        ap.error(f"the host key must be at least {MIN_HOST_KEY} characters; make one with:  "
                 'python3 -c "import secrets; print(secrets.token_urlsafe(24))"')
    if args.accounts and not args.host_key:
        ap.error("--accounts needs a host key: whoever holds it is the admin")
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
    APP.behind_proxy = args.behind_proxy
    if args.accounts:
        APP.accounts = Accounts()
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
