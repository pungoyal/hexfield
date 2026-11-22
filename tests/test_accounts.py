"""Accounts on a public server: passkeys, sessions, who may host, and whose games are whose."""

import contextlib
import hashlib
import http.client
import io
import json
import os
import secrets
import tempfile
import threading
import unittest

from hexfield.web import passkey, server
from hexfield.web.accounts import Accounts
from hexfield.web.passkey import b64u

# made with: openssl ecparam -name prime256v1 -genkey | openssl dgst -sha256 -sign
SPKI = bytes.fromhex(
    "3059301306072a8648ce3d020106082a8648ce3d0301070342000496cb6f9dd0fb7c11299cfcca97732b7bcc279cde"
    "243f322b7637c95ac93bf2ed83ca0b6d220efef2d57125824f8fcd2becfa24b1b4b41592aeefeb6feadeda7b"
)
SIG = bytes.fromhex(
    "3045022100f0ecee0b5b9bcb748962f639bca77eee30921c8cf060bb42f12fd3c1cabdb37d022047b21a36a03962"
    "6ab45e5d951ffce5ae1d58876823bc51170fd43399555e6938"
)
MSG = b"hexfield passkey test vector"
HOST_KEY = "h" * 24


class Authenticator:
    """A passkey made in the test: what a browser and its authenticator send."""

    def __init__(self, rp="localhost"):
        self.d = secrets.randbelow(passkey.N - 1) + 1
        x, y = passkey._mul(self.d, passkey.G)
        self.spki = passkey.SPKI_PREFIX + b"\x04" + x.to_bytes(32) + y.to_bytes(32)
        self.id = secrets.token_bytes(16)
        self.rp = rp
        self.count = 0

    def sign(self, message):
        e = int.from_bytes(hashlib.sha256(message).digest())
        k = secrets.randbelow(passkey.N - 1) + 1
        r = passkey._mul(k, passkey.G)[0] % passkey.N
        s = pow(k, -1, passkey.N) * (e + r * self.d) % passkey.N

        def der(v):
            b = v.to_bytes(33).lstrip(b"\0")
            return b"\x02" + bytes([len(b) + (b[0] >> 7)]) + b"\0" * (b[0] >> 7) + b

        body = der(r) + der(s)
        return b"\x30" + bytes([len(body)]) + body

    def _auth_data(self, attested):
        self.count += 1
        flags = passkey.UP | passkey.UV | (passkey.AT if attested else 0)
        out = hashlib.sha256(self.rp.encode()).digest() + bytes([flags]) + self.count.to_bytes(4)
        if attested:
            out += bytes(16) + len(self.id).to_bytes(2) + self.id + b"(the COSE key, unread)"
        return out

    def create(self, options, origin):
        cd = json.dumps({"type": "webauthn.create", "challenge": options["challenge"], "origin": origin}).encode()
        return {
            "clientData": b64u(cd),
            "authData": b64u(self._auth_data(True)),
            "publicKey": b64u(self.spki),
            "alg": -7,
        }

    def get(self, options, origin):
        cd = json.dumps({"type": "webauthn.get", "challenge": options["challenge"], "origin": origin}).encode()
        ad = self._auth_data(False)
        return {
            "id": b64u(self.id),
            "clientData": b64u(cd),
            "authData": b64u(ad),
            "signature": b64u(self.sign(ad + hashlib.sha256(cd).digest())),
        }


class SignatureTests(unittest.TestCase):
    def test_matches_openssl(self):
        key = passkey.public_key(SPKI)
        self.assertTrue(passkey.verify(key, MSG, SIG))
        self.assertFalse(passkey.verify(key, MSG + b"!", SIG))
        self.assertFalse(passkey.verify(passkey.public_key(Authenticator().spki), MSG, SIG))
        bent = bytearray(SIG)
        bent[-1] ^= 1
        self.assertFalse(passkey.verify(key, MSG, bytes(bent)))

    def test_round_trip_and_malformed_input(self):
        a = Authenticator()
        key = passkey.public_key(a.spki)
        for i in range(5):
            self.assertTrue(passkey.verify(key, b"m%d" % i, a.sign(b"m%d" % i)))
        for bad in (b"", b"\x30\x00", SIG[:-1], SIG + b"\0", b"\x31" + SIG[1:]):
            with self.assertRaises(passkey.Invalid):
                passkey.verify(key, MSG, bad)
        off_curve = SPKI[:-1] + bytes([SPKI[-1] ^ 1])
        for bad in (off_curve, SPKI[:-1], b"\0" + SPKI[1:]):
            with self.assertRaises(passkey.Invalid):
                passkey.public_key(bad)
        self.assertFalse(passkey.verify(key, MSG, b"\x30\x06\x02\x01\x00\x02\x01\x01"))  # r = 0


class SignCountTests(unittest.TestCase):
    def test_a_count_must_go_up_unless_the_authenticator_keeps_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            acc = Accounts(os.path.join(tmp, "accounts.db"))
            self.addCleanup(acc.close)
            acc.create("a" * 32, "Ada", b"counted", (1, 2), 5)
            self.assertFalse(acc.used(b"counted", 5))
            self.assertTrue(acc.used(b"counted", 6))
            self.assertFalse(acc.used(b"counted", 6))
            acc.create("b" * 32, "Bo", b"uncounted", (1, 2), 0)
            self.assertTrue(acc.used(b"uncounted", 0))
            self.assertTrue(acc.used(b"uncounted", 0))


class Browser:
    """One browser: its cookies, sent to the server as `localhost`."""

    def __init__(self, port):
        self.port, self.cookies = port, {}
        self.origin = f"http://localhost:{port}"

    def call(self, route, body=None, **headers):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {
            "Host": f"localhost:{self.port}",
            "Content-Type": "application/json",
            "Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items()),
            **headers,
        }
        c.request(
            "GET" if body is None else "POST",
            "/api/" + route,
            body=None if body is None else json.dumps(body).encode(),
            headers=headers,
        )
        r = c.getresponse()
        for h in r.headers.get_all("Set-Cookie") or []:
            k, _, v = h.split(";")[0].partition("=")
            if v:
                self.cookies[k] = v
            else:
                self.cookies.pop(k, None)
        out = {"status": r.status, **json.loads(r.read() or b"{}")}
        c.close()
        return out

    def sign_up(self, name, a):
        return self.call("signup", a.create(self.call("signup-options", {"name": name}), self.origin))

    def sign_in(self, a):
        return self.call("signin", a.get(self.call("signin-options", {}), self.origin))


class AccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        server.APP.accounts = Accounts(os.path.join(self.dir.name, "accounts.db"))
        server.APP.set_host_key(HOST_KEY)
        self.admin = Browser(self.port)
        self.assertEqual(self.admin.call("host", {"key": HOST_KEY})["status"], 200)

    def tearDown(self):
        server.APP.accounts.close()
        server.APP.accounts = None
        server.APP.set_host_key(None)
        server.APP.buckets.clear()
        server.APP.challenges.clear()
        self.dir.cleanup()

    def account(self, name, host=False):
        b, a = Browser(self.port), Authenticator()
        self.assertEqual(b.sign_up(name, a)["status"], 200)
        if host:
            uid = next(u["id"] for u in self.admin.call("accounts")["users"] if u["name"] == name)
            self.admin.call("accounts", {"id": uid, "host": True})
        return b, a

    def test_sign_up_sign_in_and_out(self):
        b, a = Browser(self.port), Authenticator()
        self.assertEqual(b.call("me")["user"], None)
        self.assertEqual(b.call("new", {"seed": 1})["error"], "sign in to host games")
        self.assertEqual(b.call("signup-options", {"name": "  "})["status"], 400)
        self.assertEqual(b.sign_up("  Ada   Lovelace of London ", a)["status"], 200)
        me = b.call("me")
        self.assertEqual(
            (me["user"], me["host"], me["admin"]), ({"name": "Ada Lovelace o", "host": False}, False, False)
        )
        self.assertEqual(b.call("new", {"seed": 1})["error"], "hosting games needs the admin's approval")
        self.assertEqual(b.call("signout", {})["status"], 200)
        self.assertEqual(b.call("me")["user"], None)
        self.assertEqual(b.sign_in(a)["status"], 200)
        self.assertEqual(b.call("me")["user"]["name"], "Ada Lovelace o")
        # the same passkey can't make a second account
        self.assertEqual(Browser(self.port).sign_up("Ada again", a)["status"], 400)

    def test_a_sign_in_must_be_fresh_and_for_this_site(self):
        b, a = self.account("Ada")
        opts = b.call("signin-options", {})
        body = a.get(opts, b.origin)
        self.assertEqual(b.call("signin", body)["status"], 200)
        self.assertEqual(b.call("signin", body)["status"], 403)  # a challenge works once
        self.assertEqual(b.call("signin", a.get(b.call("signin-options", {}), "https://evil.example"))["status"], 400)
        self.assertEqual(b.call("signin", a.get({"challenge": b64u(b"made up")}, b.origin))["status"], 403)
        stranger = Authenticator()
        self.assertEqual(b.call("signin", stranger.get(b.call("signin-options", {}), b.origin))["status"], 403)
        other_site = Authenticator(rp="evil.example")
        other_site.id, other_site.d = a.id, a.d
        self.assertEqual(b.call("signin", other_site.get(b.call("signin-options", {}), b.origin))["status"], 400)
        forged = a.get(b.call("signin-options", {}), b.origin)
        forged["signature"] = b64u(Authenticator().sign(b"something else"))
        self.assertEqual(b.call("signin", forged)["status"], 400)
        a.count = 1  # behind the count the server saw: a copied passkey
        self.assertEqual(b.call("signin", a.get(b.call("signin-options", {}), b.origin))["status"], 403)
        self.assertEqual(b.call("signin", {"id": "%%", "clientData": "", "authData": ""})["status"], 403)

    def test_session_cookie(self):
        b, _ = self.account("Ada")
        token = b.cookies["hexfield_session"]
        self.assertGreaterEqual(len(token), 40)
        b.cookies["hexfield_session"] = token[:-1] + ("A" if token[-1] != "A" else "B")
        self.assertIsNone(b.call("me")["user"])
        code = b.call("signup", {}, Origin="https://evil.example")["status"]
        self.assertEqual(code, 403)  # no cross-site posts

    def test_the_admin_decides_who_hosts(self):
        b, _ = self.account("Ada")
        self.assertEqual(b.call("accounts")["status"], 403)
        self.assertEqual(Browser(self.port).call("accounts")["status"], 403)
        users = self.admin.call("accounts")["users"]
        self.assertEqual([(u["name"], u["host"]) for u in users], [("Ada", False)])
        self.assertEqual(self.admin.call("accounts", {"id": "nobody", "host": True})["status"], 400)
        self.admin.call("accounts", {"id": users[0]["id"], "host": True})
        self.assertTrue(b.call("me")["host"])
        gid = b.call("new", {"opponents": 2, "seed": 3})["gid"]
        self.assertEqual(b.call("state")["gid"], gid)  # their newest game, without naming it
        self.admin.call("accounts", {"id": users[0]["id"], "host": False})
        self.assertEqual(b.call("games")["status"], 403)

    def test_each_account_has_its_own_games(self):
        ada, _ = self.account("Ada", host=True)
        bob, _ = self.account("Bob", host=True)
        gid = ada.call("new", {"opponents": 2, "seed": 3})["gid"]
        server.APP.games[gid].save()
        self.assertEqual([g["id"] for g in ada.call("games")["games"]], [gid])
        self.assertEqual(bob.call("games")["games"], [])
        self.assertNotIn(gid, [g["id"] for g in self.admin.call("games")["games"]])
        self.assertNotIn("gid", bob.call("state"))  # no game of Bob's own
        self.assertTrue(bob.call(f"state?g={gid}")["join"])  # someone else's: the lobby
        self.assertEqual(bob.call("resume", {"id": gid})["status"], 400)
        self.assertEqual(bob.call("delete", {"id": gid})["deleted"], False)
        self.assertEqual(bob.call(f"export?id={gid}")["status"], 400)
        self.assertEqual(bob.call(f"replay?id={gid}")["status"], 400)
        self.assertIn(gid, server.APP.games)
        server.APP.games.pop(gid).close()  # off to disk: still Ada's alone
        self.assertEqual(bob.call(f"state?g={gid}"), {"status": 200, "nogame": True})
        self.assertEqual(ada.call(f"state?g={gid}")["gid"], gid)
        self.assertEqual(ada.call("delete", {"id": gid})["deleted"], True)

    def test_joining_needs_an_account(self):
        ada, _ = self.account("Ada", host=True)
        gid = ada.call("new", {"mode": "online", "humans": 2, "opponents": 1, "seed": 9})["gid"]
        try:
            self.assertEqual(Browser(self.port).call(f"join?g={gid}", {"name": "Eve"})["status"], 403)
            bob, _ = self.account("Bob")  # not a host: joining is for everyone with an account
            st = bob.call(f"join?g={gid}", {"name": "Not Bob"})
            self.assertEqual(st["players"][st["human"]]["name"], "Bob")
        finally:
            ada.call("delete", {"id": gid})

    def test_sign_ups_are_limited_per_address(self):
        for i in range(int(server.SIGNUP_BURST)):
            self.assertEqual(Browser(self.port).sign_up(f"P{i}", Authenticator())["status"], 200)
        self.assertEqual(Browser(self.port).sign_up("One more", Authenticator())["status"], 429)
        self.assertEqual(len(self.admin.call("accounts")["users"]), server.SIGNUP_BURST)

    def test_a_host_plays_under_their_account_name(self):
        ada, _ = self.account("Ada", host=True)
        st = ada.call("new", {"opponents": 2, "seed": 3, "name": "Mallory"})
        online = ada.call("new", {"mode": "online", "humans": 2, "opponents": 1, "names": ["Mallory", "Eve"]})
        try:
            self.assertEqual(st["players"][st["human"]]["name"], "Ada")
            self.assertEqual(online["players"][online["human"]]["name"], "Ada")
            self.assertIsNotNone(self.admin.call("accounts")["users"][0]["seen"])  # signing up is signing in
        finally:
            ada.call("delete", {"id": st["gid"]})
            ada.call("delete", {"id": online["gid"]})

    def test_names_are_unique(self):
        self.account("Ada")
        self.assertEqual(Browser(self.port).call("signup-options", {"name": "ADA"})["status"], 400)

    def test_open_prompts_are_limited_per_address(self):
        b = Browser(self.port)
        for _ in range(server.MAX_CHALLENGES_EACH):
            self.assertEqual(b.call("signin-options", {})["status"], 200)
        self.assertEqual(b.call("signin-options", {})["status"], 403)
        server.APP.challenge("signin", "198.51.100.1")  # others still can

    def test_a_failed_sign_up_costs_no_allowance(self):
        b, a = Browser(self.port), Authenticator()
        for _ in range(int(server.SIGNUP_BURST) + 2):
            self.assertEqual(
                b.call("signup", a.create(b.call("signup-options", {"name": "Ada"}), "https://evil.example"))["status"],
                400,
            )
        self.assertEqual(b.sign_up("Ada", a)["status"], 200)

    def test_the_admin_can_delete_an_account(self):
        ada, a = self.account("Ada", host=True)
        gid = ada.call("new", {"mode": "online", "humans": 2, "opponents": 1, "seed": 9})["gid"]
        uid = self.admin.call("accounts")["users"][0]["id"]
        self.assertEqual(self.admin.call("accounts", {"id": uid, "delete": True})["users"], [])
        self.assertNotIn(gid, server.APP.games)
        self.assertFalse(os.path.exists(os.path.join(os.environ["HEXFIELD_HOME"], "games", "users", uid)))
        self.assertIsNone(ada.call("me")["user"])  # its session went too
        self.assertEqual(ada.sign_in(a)["status"], 403)  # and its passkey
        self.assertEqual(self.admin.call("accounts", {"id": uid, "delete": True})["status"], 400)

    def test_one_host_cant_push_the_others_games_out(self):
        ada, _ = self.account("Ada", host=True)
        bob, _ = self.account("Bob", host=True)
        mine = bob.call("new", {"opponents": 2, "seed": 1})
        self.assertTrue(mine["own"])
        made = [ada.call("new", {"opponents": 2, "seed": i})["gid"] for i in range(server.MAX_LIVE_EACH + 3)]
        try:
            self.assertIn(mine["gid"], server.APP.games)
            self.assertEqual(
                sum(g.owner != "" and g.gid in made for g in server.APP.games.values()), server.MAX_LIVE_EACH
            )
            self.assertFalse(bob.call(f"state?g={made[-1]}").get("own"))
        finally:
            for gid in made + [mine["gid"]]:
                (ada if gid in made else bob).call("delete", {"id": gid})

    def test_accounts_need_a_host_key(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            server.main(["--accounts", "--no-browser", "--port", "0"])


class ProxyTests(unittest.TestCase):
    def remote(self, peer, **headers):
        h = server.Handler.__new__(server.Handler)
        h.client_address = (peer, 4000)
        h.headers = {k.replace("_", "-"): v for k, v in headers.items()}
        return h.remote()

    def test_the_client_address_comes_from_a_trusted_proxy_only(self):
        fwd = {"X_Forwarded_For": "198.51.100.4"}
        self.assertEqual(self.remote("172.18.0.2", **fwd), "172.18.0.2")  # anyone can send the header
        self.assertEqual(self.remote("127.0.0.1", **fwd), "198.51.100.4")  # a proxy on this machine
        server.APP.behind_proxy = True
        try:
            self.assertEqual(self.remote("172.18.0.2", **fwd), "198.51.100.4")
            self.assertEqual(self.remote("172.18.0.2", X_Forwarded_For="6.6.6.6, 198.51.100.4"), "198.51.100.4")
            self.assertEqual(self.remote("172.18.0.2"), "172.18.0.2")
            h = server.Handler.__new__(server.Handler)
            h.client_address, h.headers = ("172.18.0.2", 4000), {"X-Forwarded-For": "2001:db8:1:2:3:4:5:6"}
            self.assertEqual(h.client(), "2001:db8:1:2::/64")  # one network, however many addresses
        finally:
            server.APP.behind_proxy = False


if __name__ == "__main__":
    unittest.main()
