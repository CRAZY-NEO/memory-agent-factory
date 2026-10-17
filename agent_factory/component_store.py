"""Transactional component versions, immutable evidence and binding history."""

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .component_models import NAME
from .knowledge import digest


class LifecycleConflict(ValueError):
    pass


def canonical(value):
    # Some trusted contracts assign meaning to mapping order (field_formats).
    # Preserve that order in defaults, rollback snapshots and idempotency hashes.
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


class ComponentStore:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.path = self.workspace / 'state/components/catalog.sqlite'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY, component TEXT NOT NULL, sequence INTEGER NOT NULL,
                    status TEXT NOT NULL, metadata TEXT NOT NULL, seal TEXT NOT NULL,
                    created REAL NOT NULL, UNIQUE(component,sequence));
                CREATE TABLE IF NOT EXISTS bindings (
                    slot TEXT PRIMARY KEY, version TEXT NOT NULL, revision INTEGER NOT NULL,
                    parameters TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, slot TEXT,
                    metadata TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, status TEXT NOT NULL,
                    result TEXT, created REAL NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def name(value):
        if not isinstance(value, str) or not re.fullmatch(NAME, value):
            raise ValueError('Invalid component or slot name')
        return value

    def add_version(self, component, metadata, status='approved'):
        self.name(component)
        if status not in {'approved', 'rejected'}:
            raise ValueError('Invalid release status')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            sequence = db.execute('SELECT coalesce(max(sequence),0)+1 FROM versions WHERE component=?', (component,)).fetchone()[0]
            uid = component + '@' + str(sequence)
            record = {**metadata, 'version': uid, 'component': component, 'sequence': sequence, 'status': status}
            encoded = canonical(record)
            db.execute('INSERT INTO versions VALUES (?,?,?,?,?,?,?)',
                       (uid, component, sequence, status, encoded, digest(encoded), time.time()))
        return record

    @staticmethod
    def _version(row):
        if row is None:
            raise ValueError('Component version not found')
        if digest(row['metadata']) != row['seal']:
            raise ValueError('Component metadata integrity check failed')
        value = json.loads(row['metadata'])
        if value['status'] != row['status'] or value['version'] != row['id']:
            raise ValueError('Component metadata identity mismatch')
        return value

    def version(self, uid):
        with self.connect() as db:
            return self._version(db.execute('SELECT * FROM versions WHERE id=?', (uid,)).fetchone())

    def versions(self):
        with self.connect() as db:
            return [self._version(row) for row in db.execute('SELECT * FROM versions ORDER BY created DESC,id DESC')]

    def binding(self, slot):
        self.name(slot)
        with self.connect() as db:
            row = db.execute('SELECT * FROM bindings WHERE slot=?', (slot,)).fetchone()
        return {**dict(row), 'parameters': json.loads(row['parameters'])} if row else None

    def bindings(self):
        with self.connect() as db:
            names = [row['slot'] for row in db.execute('SELECT slot FROM bindings ORDER BY slot')]
        return [self.binding(name) for name in names]

    def switch(self, slot, version, expected_revision, parameters, *, rollback=False):
        """Compare-and-swap the binding and append its old/new snapshot atomically."""
        self.name(slot)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            target = self._version(db.execute('SELECT * FROM versions WHERE id=?', (version,)).fetchone())
            if target['status'] != 'approved':
                raise ValueError('Cannot activate a rejected version')
            old = db.execute('SELECT * FROM bindings WHERE slot=?', (slot,)).fetchone()
            revision = old['revision'] if old else 0
            if revision != expected_revision:
                raise LifecycleConflict('Binding changed concurrently; refresh its revision before retrying')
            prior = {**dict(old), 'parameters': json.loads(old['parameters'])} if old else None
            new = {'slot': slot, 'version': version, 'revision': revision + 1, 'parameters': parameters}
            db.execute('INSERT INTO bindings VALUES (?,?,?,?) ON CONFLICT(slot) DO UPDATE SET '
                       'version=excluded.version,revision=excluded.revision,parameters=excluded.parameters',
                       (slot, version, new['revision'], canonical(parameters)))
            db.execute('INSERT INTO events(kind,slot,metadata,created) VALUES (?,?,?,?)',
                       ('rollback' if rollback else 'activate', slot, canonical({'previous': prior, 'current': new}), time.time()))
        return new

    def event(self, kind, metadata, slot=None):
        with self.connect() as db:
            db.execute('INSERT INTO events(kind,slot,metadata,created) VALUES (?,?,?,?)',
                       (kind, slot, canonical(metadata), time.time()))

    def history(self, slot=None):
        if slot:
            self.name(slot)
        with self.connect() as db:
            rows = db.execute('SELECT * FROM events' + (' WHERE slot=?' if slot else '') + ' ORDER BY id',
                              (slot,) if slot else ()).fetchall()
        return [{**dict(row), 'metadata': json.loads(row['metadata'])} for row in rows]

    def begin(self, uid, request):
        request_hash = digest(canonical(request))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM operations WHERE id=?', (uid,)).fetchone()
            if row:
                if row['request_hash'] != request_hash:
                    raise LifecycleConflict('Idempotency key was already used with a different request')
                if row['status'] == 'running':
                    raise LifecycleConflict('This operation is already running')
                return json.loads(row['result'])
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?)', (uid, request_hash, 'running', None, time.time()))
        return None

    def finish(self, uid, result):
        with self.connect() as db:
            db.execute('UPDATE operations SET status=?,result=? WHERE id=? AND status=?',
                       ('finished', canonical(result), uid, 'running'))
