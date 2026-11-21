"""Durable, minimal reply outbox for a single host with a persistent volume."""
import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

RETAIN_SECONDS = 8 * 86400


@dataclass(frozen=True, repr=False)
class Reply:
    event_id: str
    route: dict
    language: str
    action: str
    deadline: float


@dataclass(frozen=True, repr=False)
class Job:
    key: str
    route: dict
    language: str
    action: str
    attempts: int
    deadline: float


class Outbox:
    def __init__(self, channel, directory, master_key):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / f"{channel}.sqlite3"
        # Create with private permissions before SQLite first opens WAL/shm files.
        os.close(os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600))
        os.chmod(self.path, 0o600)
        self.channel = channel.encode()
        self.index_key = self._derive(master_key, b"event")
        self.cipher = AESGCM(self._derive(master_key, b"encryption"))
        verifier = self._derive(master_key, b"volume-check").hex()
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO settings VALUES ('key', ?)", (verifier,))
            existing = db.execute("SELECT value FROM settings WHERE name='key'").fetchone()[0]
            if not hmac.compare_digest(existing, verifier):
                raise ValueError(f"State key does not match existing {channel} volume")
            db.execute("""CREATE TABLE IF NOT EXISTS replies (
                          id TEXT PRIMARY KEY, sealed BLOB, status TEXT NOT NULL,
                          created REAL NOT NULL, deadline REAL NOT NULL, ready REAL NOT NULL,
                          claimed REAL, attempts INTEGER NOT NULL DEFAULT 0)""")
            db.execute("CREATE INDEX IF NOT EXISTS pending ON replies(status, ready, deadline)")

    def _derive(self, master, purpose):
        return hmac.digest(master, b"pulse-channels-v1:" + self.channel + b":" + purpose, "sha256")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=3, isolation_level=None)
        db.execute("PRAGMA secure_delete=ON")
        return db

    def add(self, replies, now):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for item in replies:
                if item.deadline <= now:
                    continue
                key = hmac.new(self.index_key, item.event_id.encode(), hashlib.sha256).hexdigest()
                nonce = os.urandom(12)
                # The only sensitive field is the route, never message content/media.
                data = json.dumps([item.route, item.language, item.action], separators=(",", ":")).encode()
                sealed = nonce + self.cipher.encrypt(nonce, data, key.encode())
                db.execute("""INSERT OR IGNORE INTO replies
                              (id,sealed,status,created,deadline,ready) VALUES (?,?, 'pending',?,?,?)""",
                           (key, sealed, now, item.deadline, now))

    def claim(self, now):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            # After a worker crash, the provider may already have the request.
            db.execute("UPDATE replies SET status='unknown', sealed=NULL WHERE status='sending' AND claimed < ?",
                       (now - 30,))
            db.execute("UPDATE replies SET status='expired', sealed=NULL WHERE status='pending' AND deadline<=?",
                       (now,))
            db.execute("DELETE FROM replies WHERE created < ?", (now - RETAIN_SECONDS,))
            row = db.execute("""SELECT id,sealed,attempts,deadline FROM replies
                               WHERE status='pending' AND ready<=? ORDER BY deadline, created LIMIT 1""",
                             (now,)).fetchone()
            if row is None:
                return None
            key, sealed, attempts, deadline = row
            try:
                route, language, action = json.loads(self.cipher.decrypt(sealed[:12], sealed[12:], key.encode()))
            except (InvalidTag, ValueError, TypeError):
                db.execute("UPDATE replies SET status='rejected', sealed=NULL WHERE id=?", (key,))
                return None
            db.execute("UPDATE replies SET status='sending', claimed=?, attempts=attempts+1 WHERE id=?",
                       (now, key))
            return Job(key, route, language, action, attempts + 1, deadline)

    def complete(self, job, result, now, retry_after=0):
        with closing(self._connect()) as db:
            if result == "retry":
                delay = max(retry_after, 2 ** job.attempts)
                if job.attempts < 4 and now + delay < job.deadline:
                    db.execute("""UPDATE replies SET status='pending', ready=?
                                  WHERE id=? AND status='sending' AND attempts=?""",
                               (now + delay, job.key, job.attempts))
                    return
                result = "expired" if now + delay >= job.deadline else "rejected"
            if result not in {"accepted", "unknown", "rejected", "expired"}:
                raise ValueError("Invalid delivery state")
            db.execute("UPDATE replies SET status=?, sealed=NULL WHERE id=? AND status='sending' AND attempts=?",
                       (result, job.key, job.attempts))
