"""OAuth state in one SQLite file: registered clients, authorization codes, access and refresh tokens.

Codes and tokens are stored by their SHA-256, so the file alone can't be used to call the server. Wiping it revokes
everything; Claude then asks to connect again."""

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

MAX_CLIENTS = 20  # registration is open to anyone who finds the URL, so the oldest are dropped past this


def digest(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        self._db.executescript("""
            create table if not exists clients (client_id text primary key, data text not null, created real not null);
            create table if not exists grants (
                hash text primary key, kind text not null, data text not null, expires real not null);
            create table if not exists totp_used (step integer primary key);
        """)

    def _run(self, sql, *args):
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def save_client(self, client_id, data):
        self._run("insert or replace into clients values (?, ?, ?)", client_id, json.dumps(data), time.time())
        self._run("delete from clients where client_id not in (select client_id from clients order by created desc limit ?)", MAX_CLIENTS)

    def client(self, client_id):
        rows = self._run("select data from clients where client_id = ?", client_id)
        return json.loads(rows[0][0]) if rows else None

    def save(self, kind, secret, data, expires):
        self._run("insert or replace into grants values (?, ?, ?, ?)", digest(secret), kind, json.dumps(data), expires)

    def get(self, kind, secret):
        rows = self._run("select data from grants where hash = ? and kind = ? and expires > ?", digest(secret), kind, time.time())
        return json.loads(rows[0][0]) if rows else None

    def delete(self, secret):
        self._run("delete from grants where hash = ?", digest(secret))

    def delete_where(self, kind, key, value):
        """Drop every grant of a kind whose data has `key` equal to `value` (e.g. the access tokens of a refresh token)."""
        self._run("delete from grants where kind = ? and json_extract(data, ?) = ?", kind, f"$.{key}", value)

    def prune(self):
        self._run("delete from grants where expires <= ?", time.time())
        self._run("delete from totp_used where step < ?", int(time.time() // 30) - 10)

    def use_totp_step(self, step):
        """True the first time a TOTP time step is used, so an overheard code can't be replayed."""
        with self._lock:
            return self._db.execute("insert or ignore into totp_used values (?)", (step,)).rowcount == 1
