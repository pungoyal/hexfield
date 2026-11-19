"""Player accounts for a public server: names, passkeys and sign-in sessions.

One SQLite file beside the saved games ($HEXFIELD_HOME/accounts.db). An account's games
are saved in a folder of its own (see store.py). A new account can join the games it
is invited to; the admin (whoever holds the host key) decides who may host games.
"""

import hashlib
import os
import secrets
import sqlite3
import threading
import time

from .. import store

SESSION_DAYS = 30
SCHEMA = """
create table if not exists users (
  id text primary key,          -- 32 hex digits: the passkey's user handle, and the games folder
  name text not null,
  host integer not null default 0,
  created real not null
);
create table if not exists passkeys (
  id blob primary key,          -- the credential id
  user_id text not null references users(id) on delete cascade,
  public_key blob not null,     -- 64 bytes: x then y
  sign_count integer not null,
  created real not null,
  used real
);
create unique index if not exists users_name on users (name collate nocase);
create table if not exists sessions (
  token_hash blob primary key,  -- sha256 of the cookie, so the file holds no live session
  user_id text not null references users(id) on delete cascade,
  expires real not null
);
"""


def _hash(token):
    return hashlib.sha256(token.encode()).digest()


class Accounts:
    def __init__(self, path=None):
        if path is None:
            os.makedirs(store.home(), mode=0o700, exist_ok=True)
            path = os.path.join(store.home(), "accounts.db")
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("pragma journal_mode = wal")
            self.db.execute("pragma foreign_keys = on")
            self.db.executescript(SCHEMA)
            self.db.execute("delete from sessions where expires < ?", (time.time(),))

    def _q(self, sql, *args):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def named(self, name):
        return bool(self._q("select 1 from users where name = ? collate nocase", name))

    def delete(self, user_id):
        """Remove an account with its passkeys and sessions. False when there is none."""
        with self.lock:
            return self.db.execute("delete from users where id = ?", (str(user_id),)).rowcount == 1

    def count(self):
        return self._q("select count(*) from users")[0][0]

    def create(self, user_id, name, cred_id, key, sign_count):
        """A new account with its first passkey."""
        now = time.time()
        with self.lock:
            self.db.execute("begin")
            try:
                self.db.execute("insert into users (id, name, created) values (?, ?, ?)", (user_id, name, now))
                self.db.execute("insert into passkeys (id, user_id, public_key, sign_count, created, used) values (?, ?, ?, ?, ?, ?)",
                                (cred_id, user_id, key[0].to_bytes(32) + key[1].to_bytes(32), sign_count, now, now))
                self.db.execute("commit")
            except BaseException:
                self.db.execute("rollback")
                raise

    def passkey(self, cred_id):
        """-> (user id, (x, y), sign count) or None."""
        rows = self._q("select user_id, public_key, sign_count from passkeys where id = ?", cred_id)
        if not rows:
            return None
        uid, raw, count = rows[0]
        return uid, (int.from_bytes(raw[:32]), int.from_bytes(raw[32:])), count

    def used(self, cred_id, sign_count):
        self._q("update passkeys set sign_count = ?, used = ? where id = ?", sign_count, time.time(), cred_id)

    def start_session(self, user_id):
        token = secrets.token_urlsafe(32)
        self._q("insert into sessions (token_hash, user_id, expires) values (?, ?, ?)",
                _hash(token), user_id, time.time() + SESSION_DAYS * 86400)
        return token

    def end_session(self, token):
        self._q("delete from sessions where token_hash = ?", _hash(token))

    def user(self, token):
        """The signed-in account for a session cookie: {"id", "name", "host"}, or None."""
        if not token:
            return None
        rows = self._q("select u.id, u.name, u.host from sessions s join users u on u.id = s.user_id "
                       "where s.token_hash = ? and s.expires > ?", _hash(token), time.time())
        return {"id": rows[0][0], "name": rows[0][1], "host": bool(rows[0][2])} if rows else None

    def users(self):
        rows = self._q("select u.id, u.name, u.host, u.created, max(p.used) from users u "
                       "left join passkeys p on p.user_id = u.id group by u.id order by u.created desc")
        return [{"id": r[0], "name": r[1], "host": bool(r[2]), "created": r[3], "seen": r[4]} for r in rows]

    def set_host(self, user_id, on):
        """Let an account host games, or stop it. False when there is no such account."""
        with self.lock:
            return self.db.execute("update users set host = ? where id = ?", (int(bool(on)), str(user_id))).rowcount == 1

    def close(self):
        with self.lock:
            self.db.close()
