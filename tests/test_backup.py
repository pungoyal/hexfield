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

    def test_keeps_30_days_and_never_fewer_than_7(self):
        day = 86400
        start = 1767225600  # 2026-01-01T00:00:00Z
        for n in [0, 1, 2, 40, 41, 50, 51, 52, 53, 54, 55]:  # days since start
            with mock.patch.object(backup.time, "time", return_value=start + n * day):
                backup.backup(self.home, self.dest)
        kept = sorted(os.listdir(self.dest))
        # at day 55, days 25 onwards are within 30 days: 40, 41 and 50-55
        self.assertEqual(len(kept), 8)
        self.assertEqual(kept[0], "hexfield-20260210T000000Z.tar.gz")  # day 40
        with mock.patch.object(backup.time, "time", return_value=start + 200 * day):
            backup.backup(self.home, self.dest)
        self.assertEqual(len(os.listdir(self.dest)), 7)

    def test_a_file_the_server_deletes_mid_backup_is_skipped(self):
        walk = os.walk

        def with_a_ghost(top):
            for root, dirs, files in walk(top):
                yield root, dirs, [*files, "gone.json"]

        with mock.patch.object(backup.os, "walk", with_a_ghost):
            path = backup.backup(self.home, self.dest)
        with tarfile.open(path) as tar:
            self.assertNotIn("games/gone.json", tar.getnames())

    def test_a_failed_backup_leaves_nothing(self):
        open(os.path.join(self.home, "accounts.db"), "w").close()
        failing = mock.patch.object(backup.sqlite3, "connect", side_effect=sqlite3.OperationalError("disk I/O"))
        with failing, self.assertRaises(sqlite3.OperationalError):
            backup.backup(self.home, self.dest)
        self.assertEqual(os.listdir(self.dest), [])


if __name__ == "__main__":
    unittest.main()
