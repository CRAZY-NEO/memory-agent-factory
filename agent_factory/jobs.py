"""Persistent job queue shared by the HTTPS gateway and one trusted execution worker."""

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class QueueFull(RuntimeError):
    pass


class JobStore:
    def __init__(self, workspace):
        self.path = Path(workspace) / "state" / "jobs" / "jobs.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            # Rollback journals avoid WAL shared-memory coherence across a Docker bind mount.
            # The queue has one writer and short transactions; WAL is unnecessary here.
            db.execute("PRAGMA journal_mode=DELETE")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    result TEXT, error TEXT);
                CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created_at);
            """)
            # Gateway and worker can start together against an old queue schema.
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
            if 'run_id' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN run_id TEXT')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def enqueue(self, kind, payload, max_pending=20):
        if kind not in {"factory", "search", "ingest"}:
            raise ValueError("Unsupported job type")
        uid, now = uuid.uuid4().hex, time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            if count >= max_pending:
                raise QueueFull("Task queue is full; retry later")
            db.execute("INSERT INTO jobs (id,kind,payload,status,created_at,updated_at,result,error) VALUES (?,?,?,?,?,?,NULL,NULL)",
                       (uid, kind, json.dumps(payload, ensure_ascii=False), "queued", now, now))
        return uid

    def get(self, uid, internal=False):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (uid,)).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["result"] = json.loads(record["result"]) if record["result"] else None
        if internal:
            record["payload"] = json.loads(record["payload"])
        else:
            record.pop("payload")
        return record

    def recent(self, limit=20):
        with self.connect() as db:
            rows = db.execute('SELECT id FROM jobs ORDER BY created_at DESC,id DESC LIMIT ?', (limit,)).fetchall()
        return [self.get(row['id']) for row in rows]

    def attach_run(self, uid, run_id):
        with self.connect() as db:
            cursor = db.execute("UPDATE jobs SET run_id=? WHERE id=? AND status='running'", (run_id, uid))
            if cursor.rowcount != 1:
                raise RuntimeError('Cannot attach a run to an unclaimed job')

    def claim(self, lease_seconds=120):
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE jobs SET status='failed',error='Worker lease expired; resubmit explicitly',updated_at=? WHERE status='running' AND updated_at<?",
                       (now, now - lease_seconds))
            # Enforces a single active job even if two workers are started accidentally.
            if db.execute("SELECT 1 FROM jobs WHERE status='running' LIMIT 1").fetchone():
                return None
            row = db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running',updated_at=? WHERE id=?", (now, row["id"]))
            uid = row["id"]
        return self.get(uid, internal=True)

    def heartbeat(self, uid):
        with self.connect() as db:
            db.execute("UPDATE jobs SET updated_at=? WHERE id=? AND status='running'", (time.time(), uid))

    def finish(self, uid, result=None, error=None):
        status = "failed" if error else "succeeded"
        with self.connect() as db:
            cursor = db.execute("UPDATE jobs SET status=?,updated_at=?,result=?,error=? WHERE id=? AND status='running'",
                                (status, time.time(), json.dumps(result, ensure_ascii=False), error, uid))
            if cursor.rowcount != 1:
                raise RuntimeError("Job no longer owns a running lease")
