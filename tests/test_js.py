"""The browser client's own tests, run from the Python suite.

* tests/js/*.test.mjs  - unit tests of the DOM-free logic
* tools/e2e.mjs        - a real browser plays through the main screens (needs Chrome or
                         Chromium; opt in with HEXFIELD_BROWSER_TESTS=1 because it takes a while)
* tools/e2e-accounts.mjs - three browsers sign up, host and join with passkeys (the same)

Both need Node 26.

Both are skipped, not failed, on a machine without the tools.
"""
import os
import pathlib
import shutil
import subprocess
import threading
import unittest
from http.server import ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
NODE_MIN = 26


def node_major():
    try:
        return int(subprocess.run([NODE, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v").split(".")[0])
    except Exception:
        return 0


class ClientTests(unittest.TestCase):
    @unittest.skipUnless(NODE and node_major() >= NODE_MIN, f"needs Node {NODE_MIN}")
    def test_logic(self):
        files = sorted(str(p) for p in (ROOT / "tests/js").glob("*.test.mjs"))
        r = subprocess.run([NODE, "--test", *files], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])

    @unittest.skipUnless(NODE and node_major() >= NODE_MIN, f"needs Node {NODE_MIN}")
    def test_every_module_parses(self):
        for f in sorted((ROOT / "hexfield/web/static/js").glob("*.js")):
            r = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{f.name}: {r.stderr[-600:]}")

    def test_imports_resolve(self):
        """Every name a module imports is exported by the module it names (a typo here
        only shows up as a blank page in the browser)."""
        import re
        js = ROOT / "hexfield/web/static/js"
        exports = {}
        for f in js.glob("*.js"):
            t = f.read_text()
            names = set(re.findall(r"^export (?:async function|function|const|let) (\$\$?|\w+)", t, flags=re.M))
            for block in re.findall(r"^export \{([^}]*)\}", t, flags=re.M):
                names |= {n.split(" as ")[-1].strip() for n in block.split(",") if n.strip()}
            exports[f.name] = names
        for f in js.glob("*.js"):
            for block, src in re.findall(r"^import \{([^}]*)\} from \"\./([\w.]+)\";", f.read_text(), flags=re.M | re.S):
                self.assertIn(src, exports, f"{f.name} imports from a missing module {src}")
                for n in (x.split(" as ")[0].strip() for x in block.split(",") if x.strip()):
                    self.assertIn(n, exports[src], f"{f.name} imports {n} from {src}, which doesn't export it")

    @unittest.skipUnless(os.environ.get("HEXFIELD_BROWSER_TESTS") and NODE and node_major() >= NODE_MIN,
                         f"set HEXFIELD_BROWSER_TESTS=1 (needs Node {NODE_MIN} and Chrome or Chromium)")
    def test_in_a_real_browser(self):
        from hexfield.web import server
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{httpd.server_address[1]}/"
            r = subprocess.run([NODE, str(ROOT / "tools/e2e.mjs"), url], capture_output=True, text=True, timeout=240)
            self.assertEqual(r.returncode, 0, r.stdout[-4000:] + r.stderr[-2000:])
        finally:
            httpd.shutdown()
            httpd.server_close()

    @unittest.skipUnless(os.environ.get("HEXFIELD_BROWSER_TESTS") and NODE and node_major() >= NODE_MIN,
                         f"set HEXFIELD_BROWSER_TESTS=1 (needs Node {NODE_MIN} and Chrome or Chromium)")
    def test_accounts_in_a_real_browser(self):
        import tempfile
        from hexfield.web import server
        from hexfield.web.accounts import Accounts
        key = "admin-key-for-the-browser-test"
        with tempfile.TemporaryDirectory() as d:
            server.APP.accounts = Accounts(os.path.join(d, "accounts.db"))
            server.APP.set_host_key(key)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                url = f"http://localhost:{httpd.server_address[1]}/"
                r = subprocess.run([NODE, str(ROOT / "tools/e2e-accounts.mjs"), url, key],
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(r.returncode, 0, r.stdout[-4000:] + r.stderr[-2000:])
            finally:
                httpd.shutdown()
                httpd.server_close()
                server.APP.accounts.close()
                server.APP.accounts = None
                server.APP.set_host_key(None)


if __name__ == "__main__":
    unittest.main()
