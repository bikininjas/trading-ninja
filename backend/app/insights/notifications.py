"""File locale durable : commit avant envoi, acquittement après réponse HTTPS.

Livraison au moins une fois : une réponse perdue peut produire un doublon.
Les identifiants d'événement permettent au destinataire de le dédupliquer.
Le répertoire doit vivre sur un volume persistant, privé et sauvegardé.
"""

import fcntl
import json
import os
from pathlib import Path
import sqlite3
import uuid

from app.insights.codec import public


class NotificationOutbox:
    def __init__(self, path, *, source, mode):
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._lock = open(str(path) + ".lock", "a")
        os.chmod(self._lock.name, 0o600)
        self._db = None
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            os.chmod(path, 0o600)
            self._db = sqlite3.connect(path, timeout=5)
            self._db.execute("PRAGMA synchronous=FULL")
            with self._db:
                self._db.execute("CREATE TABLE IF NOT EXISTS stream (identity TEXT PRIMARY KEY)")
                identity = json.dumps([source, mode])
                current = self._db.execute("SELECT identity FROM stream").fetchall()
                if current and current != [(identity,)]:
                    raise ValueError("file de notifications d'une autre source ou d'un autre mode")
                self._db.execute("INSERT OR IGNORE INTO stream VALUES (?)", (identity,))
                self._db.execute("CREATE TABLE IF NOT EXISTS active (key TEXT PRIMARY KEY)")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS pending (id TEXT PRIMARY KEY, key TEXT NOT NULL, body TEXT NOT NULL)"
                )
            self.source, self.mode = source, mode
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None
        self._lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @property
    def pending(self):
        return bool(self._db.execute("SELECT 1 FROM pending LIMIT 1").fetchone())

    def observe(self, result):
        issues = {issue["key"]: issue for issue in result["issues"]}
        with self._db:
            active = {r[0] for r in self._db.execute("SELECT key FROM active")}
            for key in sorted(issues.keys() - active):
                body = public({"issue": issues[key], "observed_at": result["observation"]["observed_at"]})
                self._db.execute("INSERT INTO pending VALUES (?, ?, ?)",
                                 (str(uuid.uuid4()), key, json.dumps(body)))
            self._db.execute("DELETE FROM active")
            self._db.executemany("INSERT INTO active VALUES (?)", ((key,) for key in issues))

    async def deliver(self, url, result, notify):
        # Une livraison bornée par tour ; aucun incident n'est abandonné si le
        # destinataire ou le processus tombe. Les récidives ont des IDs distincts.
        rows = self._db.execute("SELECT id,key,body FROM pending ORDER BY rowid LIMIT 100").fetchall()
        if not rows:
            return
        await notify(url, {
            **result,
            "source": self.source,
            "mode": self.mode,
            "new_incidents": [r[1] for r in rows],
            "pending_incidents": [dict(json.loads(r[2]), event_id=r[0]) for r in rows],
        })
        with self._db:
            self._db.executemany("DELETE FROM pending WHERE id=?", ((r[0],) for r in rows))


def notification_path(source, mode):
    return Path(__file__).resolve().parents[3] / "state" / f"{source}-{mode.lower()}.sqlite3"
