"""Task and memory storage, shared safely by independent terminal processes."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path


def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "zsh-ai"


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or state_dir() / "companion.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                    created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, task TEXT NOT NULL, role TEXT NOT NULL,
                    content TEXT NOT NULL, pane TEXT, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS messages_task ON messages(task,id);
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY, scope TEXT NOT NULL, text TEXT NOT NULL,
                    source TEXT NOT NULL, created REAL NOT NULL,
                    UNIQUE(scope,text));
                CREATE TABLE IF NOT EXISTS staged (
                    pane TEXT PRIMARY KEY, task TEXT NOT NULL, command TEXT NOT NULL,
                    created REAL NOT NULL, event TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT);
            """)
            if "event" not in {r[1] for r in db.execute("PRAGMA table_info(staged)")}:
                db.execute("ALTER TABLE staged ADD COLUMN event TEXT NOT NULL DEFAULT ''")
        self.path.chmod(0o600)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def task(self, name: str) -> dict:
        name = " ".join(name.split())[:100]
        if not name:
            raise ValueError("Give the task a name.")
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO tasks VALUES (?,?,?)",
                       (uuid.uuid4().hex, name, time.time()))
            return dict(db.execute("SELECT * FROM tasks WHERE name=?", (name,)).fetchone())

    @contextlib.contextmanager
    def lock(self, name: str):
        directory = self.path.parent / "locks"
        directory.mkdir(exist_ok=True, mode=0o700)
        path = directory / hashlib.sha256(name.encode()).hexdigest()
        with path.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def get_task(self, task_id: str) -> dict | None:
        with self.db() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def tasks(self) -> list[dict]:
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM tasks ORDER BY created DESC")]

    def messages(self, task: str, limit: int = 40) -> list[dict]:
        with self.db() as db:
            rows = db.execute("SELECT * FROM messages WHERE task=? ORDER BY id DESC LIMIT ?",
                              (task, limit)).fetchall()
            return [dict(r) for r in reversed(rows)]

    def exchange(self, task: str, pane: str, user: str, assistant: str):
        with self.db() as db:
            db.executemany("INSERT INTO messages(task,role,content,pane,created) VALUES (?,?,?,?,?)",
                           [(task, role, text, pane, time.time())
                            for role, text in (("user", user), ("assistant", assistant))])

    def notes(self, task: str) -> list[dict]:
        with self.db() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM notes WHERE scope IN (?, 'global') ORDER BY id", (task,))]

    def remember(self, scope: str, text: str, source: str = "user") -> bool:
        text = " ".join(text.split()).strip()[:500]
        if not text:
            return False
        with self.db() as db:
            old = db.execute("SELECT text FROM notes WHERE scope=?", (scope,)).fetchall()
            if any(r[0].casefold().rstrip(".! ") == text.casefold().rstrip(".! ") for r in old):
                return False
            return bool(db.execute("INSERT OR IGNORE INTO notes(scope,text,source,created) VALUES (?,?,?,?)",
                                   (scope, text, source, time.time())).rowcount)

    def forget(self, note_id: int, task: str) -> bool:
        with self.db() as db:
            return bool(db.execute("DELETE FROM notes WHERE id=? AND scope IN (?, 'global')",
                                   (note_id, task)).rowcount)

    def stage(self, pane_key: str, task: str, command: str, event: str = ""):
        if not command or len(command) > 3000 or any(ord(c) < 32 or ord(c) == 127 for c in command):
            raise ValueError("Only a single command line can be staged.")
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO staged VALUES (?,?,?,?,?)",
                       (pane_key, task, command, time.time(), event))

    def take(self, pane_key: str, task: str, event: str = "") -> str:
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM staged WHERE pane=?", (pane_key,)).fetchone()
            db.execute("DELETE FROM staged WHERE pane=?", (pane_key,))
            if row and row["task"] == task and time.time() - row["created"] < 600 and (not row["event"] or row["event"] == event):
                return row["command"]
            return ""

    def import_legacy(self):
        """Keep old notes accessible without mixing the old home-directory transcript into tasks."""
        with self.db() as db:
            if db.execute("SELECT 1 FROM meta WHERE key='legacy_notes_v1'").fetchone():
                return
        memory = self.path.parent / "memory"
        if memory.exists():
            for path in memory.glob("*.md"):
                scope = "global" if path.stem == "global" else self.task("Imported: " + path.stem)["id"]
                for line in path.read_text(errors="replace").splitlines():
                    if line.startswith("- "):
                        self.remember(scope, line[2:], "imported")
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO meta VALUES ('legacy_notes_v1',?)", (json.dumps(time.time()),))
