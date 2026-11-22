"""python -m hexfield.backup SOURCE DEST [KEEP]: one .tar.gz of a Hexfield home, keeping the newest KEEP.

Run before every deploy (see compose.yaml), while the old server may still be writing.
"""

import os
import sqlite3
import sys
import tarfile
import tempfile
import time

ACCOUNTS = "accounts.db"


def backup(source, dest, keep=7):
    """Write DEST/hexfield-<UTC time>.tar.gz and delete all but the newest `keep`. -> its path."""
    name = time.strftime("hexfield-%Y%m%dT%H%M%SZ.tar.gz", time.gmtime())
    final = os.path.join(dest, name)
    partial = final + ".partial"
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the games hold seat keys
    with os.fdopen(fd, "wb") as out, tarfile.open(fileobj=out, mode="w:gz") as tar:
        for root, dirs, files in os.walk(source):
            dirs.sort()
            for f in sorted(files):
                # accounts.db goes in below as a consistent snapshot; .tmp is a game mid-write.
                if f.startswith(ACCOUNTS) or f.endswith(".tmp"):
                    continue
                path = os.path.join(root, f)
                tar.add(path, arcname=os.path.relpath(path, source))
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
    for old in sorted(f for f in os.listdir(dest) if f.startswith("hexfield-") and f.endswith(".tar.gz"))[:-keep]:
        os.unlink(os.path.join(dest, old))
    return final


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) not in (2, 3):
        sys.exit(__doc__.splitlines()[0])
    print("backed up to", backup(args[0], args[1], int(args[2]) if len(args) == 3 else 7))


if __name__ == "__main__":
    main()
