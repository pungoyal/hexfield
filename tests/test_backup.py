import os
import sqlite3
import tarfile
import tempfile
import unittest
from unittest import mock

from hexfield import backup
from hexfield.web.accounts import Accounts


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        self.dest = os.path.join(self.tmp.name, "backups")
        os.makedirs(os.path.join(self.home, "games", "users", "a" * 32))
        os.makedirs(self.dest)
        for path in ("games/1.json", "games/users/" + "a" * 32 + "/2.json", "games/half.tmp"):
            with open(os.path.join(self.home, path), "w") as f:
                f.write("{}")

    def test_takes_games_and_a_live_accounts_db(self):
        acc = Accounts(os.path.join(self.home, "accounts.db"))  # open, in WAL mode, as on the server
        self.addCleanup(acc.close)
        acc.create("b" * 32, "Ada", b"cred", (1, 2), 0)
        path = backup.backup(self.home, self.dest)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with tarfile.open(path) as tar:
            names = sorted(tar.getnames())
            self.assertEqual(names, ["accounts.db", "games/1.json", "games/users/" + "a" * 32 + "/2.json"])
            tar.extract("accounts.db", self.tmp.name, filter="data")
        db = sqlite3.connect(os.path.join(self.tmp.name, "accounts.db"))
        self.addCleanup(db.close)
        self.assertEqual(db.execute("select name from users").fetchall(), [("Ada",)])

    def test_keeps_the_newest(self):
        for n in range(4):
            with mock.patch.object(backup.time, "gmtime", return_value=(2026, 1, n + 1, 0, 0, 0, 0, 1, 0)):
                backup.backup(self.home, self.dest, keep=2)
        self.assertEqual(
            sorted(os.listdir(self.dest)), ["hexfield-20260103T000000Z.tar.gz", "hexfield-20260104T000000Z.tar.gz"]
        )


if __name__ == "__main__":
    unittest.main()
