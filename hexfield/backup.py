"""python -m hexfield.backup SOURCE DEST: one .tar.gz of a Hexfield home.

Run before every deploy (see compose.yaml), while the old server may still be writing.
Keeps 30 days of backups, and never fewer than the newest 7.
"""

import os
import sqlite3
import sys
import tarfile
import tempfile
import time

ACCOUNTS = "accounts.db"
NAME = "hexfield-%Y%m%dT%H%M%SZ.tar.gz"


def backup(source, dest, days=30, keep=7):
    """Write DEST/hexfield-<UTC time>.tar.gz and delete the old ones. -> its path."""
    now = time.time()
    final = os.path.join(dest, time.strftime(NAME, time.gmtime(now)))
    partial = final + ".partial"
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the games hold seat keys
    try:
        with os.fdopen(fd, "wb") as out, tarfile.open(fileobj=out, mode="w:gz") as tar:
            for root, dirs, files in os.walk(source):
                dirs.sort()
                for f in sorted(files):
                    # accounts.db goes in below as a consistent snapshot; .tmp is a game mid-write.
                    if f.startswith(ACCOUNTS) or f.endswith(".tmp"):
                        continue
                    path = os.path.join(root, f)
                    try:
                        tar.add(path, arcname=os.path.relpath(path, source))
                    except FileNotFoundError:  # the running server deleted it a moment ago
                        pass
            db = os.path.join(source, ACCOUNTS)
            if os.path.exists(db):
                with tempfile.TemporaryDirectory() as tmp:
                    copy = os.path.join(tmp, ACCOUNTS)
                    src = sqlite3.connect(db)
                    dst = sqlite3.connect(copy)
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                        src.close()
                    tar.add(copy, arcname=ACCOUNTS)
        os.replace(partial, final)
    except BaseException:
        os.unlink(partial)
        raise
    # By age, not count: every deploy and every rollback makes one, so a count alone would let
    # a bad release push out every backup taken before it within a few deploys.
    cutoff = time.strftime(NAME, time.gmtime(now - days * 86400))
    names = sorted(f for f in os.listdir(dest) if f.startswith("hexfield-") and f.endswith(".tar.gz"))
    for old in names[:-keep]:
        if old < cutoff:
            os.unlink(os.path.join(dest, old))
    return final


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        sys.exit(__doc__.splitlines()[0])
    print("backed up to", backup(*args))


if __name__ == "__main__":
    main()
