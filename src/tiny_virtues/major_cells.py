"""Disk-backed virtue/story labels and once-per-story full-feedback ownership. -- PI/Sol"""
from contextlib import closing
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import threading

from tiny_virtues import item_major as builder
from tiny_virtues.case_input import judging_story, require_selected_target


def story_hash(row):
    require_selected_target(row)
    assert row['person'].strip(), row['id']
    return builder.digest(dict(agent=row['person'], story=judging_story(row), selected_act=row['target_act_quote']))


def cell_key(virtue_id, story_id, input_sha, rubric_sha):
    return builder.digest([virtue_id, story_id, input_sha, rubric_sha])


def derive_contrary(virtue, vice_id):
    return dict(id=virtue['id'], item_id=vice_id, shows=virtue['against'], against=virtue['shows'],
                signed=virtue['signed'], vice_presence=-virtue['signed'], derived_from=virtue['item_id'],
                format='source-named-contrary-derived-v1',
                evidence_semantics='Derived from the named virtue, not a separate Jev answer; raw probabilities belong to the referenced virtue.')


class CellStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=60)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA wal_autocheckpoint=1000')
        self.db.execute('PRAGMA cache_size=-4096')
        self.db.execute('PRAGMA temp_store=FILE')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR IGNORE INTO metadata VALUES ('schema', 'major-cells-v1');
            CREATE TABLE IF NOT EXISTS stories (
                id TEXT PRIMARY KEY, input_sha TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS virtues (id TEXT PRIMARY KEY, rubric_sha TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS labels (
                cell_key TEXT PRIMARY KEY, virtue_id TEXT NOT NULL, story_id TEXT NOT NULL,
                input_sha TEXT NOT NULL, rubric_sha TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS labels_by_story ON labels(story_id, input_sha);
            CREATE TABLE IF NOT EXISTS full_feedback (
                story_id TEXT NOT NULL, input_sha TEXT NOT NULL, owner_virtue TEXT NOT NULL,
                complete INTEGER NOT NULL DEFAULT 0, native_path TEXT,
                PRIMARY KEY(story_id, input_sha));
            CREATE TABLE IF NOT EXISTS requests (
                ordinal INTEGER PRIMARY KEY AUTOINCREMENT, packet_key TEXT UNIQUE NOT NULL,
                packet_path TEXT NOT NULL, stage_path TEXT NOT NULL, virtue_id TEXT NOT NULL, complete INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS requests_by_virtue ON requests(virtue_id, complete);
            CREATE TABLE IF NOT EXISTS receipts (
                response_id TEXT PRIMARY KEY, request_key TEXT NOT NULL, cost_usd TEXT NOT NULL,
                receipt_path TEXT NOT NULL, cost_pico INTEGER NOT NULL);
        ''')
        assert self.db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone() == ('major-cells-v1',)

    def close(self):
        self.db.close()

    def checkpoint(self):
        with self.lock:
            timeout = self.db.execute('PRAGMA busy_timeout').fetchone()[0]
            self.db.execute('PRAGMA busy_timeout=0')
            try:
                busy, frames, written = self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            finally:
                self.db.execute(f'PRAGMA busy_timeout={timeout}')
        return dict(busy=busy, frames=frames, checkpointed=written)

    def _pages(self, sql, params=(), *, after):
        while True:
            with self.lock, closing(self.db.execute(sql, (*params, after))) as cursor:
                rows = cursor.fetchall()
            if not rows:
                return
            after = rows[-1][0]
            yield from rows

    def set_stories(self, rows):
        with self.lock, self.db:
            self.db.execute('DELETE FROM stories')
            for row in rows:
                self.db.execute('INSERT INTO stories VALUES (?, ?, ?)',
                                (row['id'], story_hash(row), json.dumps(row, ensure_ascii=False)))

    def stories(self):
        for identity, input_sha, data in self._pages(
                'SELECT id,input_sha,data FROM stories WHERE id>? ORDER BY id LIMIT 64', after=''):
            yield identity, input_sha, json.loads(data)

    def set_virtues(self, versions):
        with self.lock, self.db:
            self.db.execute('DELETE FROM virtues')
            self.db.executemany('INSERT INTO virtues VALUES (?,?)', versions.items())

    def coverage(self):
        with self.lock:
            done = self.db.execute('''SELECT COUNT(*) FROM labels l JOIN stories s ON l.story_id=s.id AND l.input_sha=s.input_sha
                                      JOIN virtues v ON l.virtue_id=v.id AND l.rubric_sha=v.rubric_sha''').fetchone()[0]
            stories = self.db.execute('SELECT COUNT(*) FROM stories').fetchone()[0]
            virtues = self.db.execute('SELECT COUNT(*) FROM virtues').fetchone()[0]
            full = self.db.execute('''SELECT COUNT(*) FROM full_feedback f JOIN stories s ON f.story_id=s.id AND f.input_sha=s.input_sha
                                      WHERE f.complete=1''').fetchone()[0]
        return dict(completed_cells=done, expected_cells=stories * virtues, stories=stories, virtues=virtues, full_feedback_stories=full)

    def get(self, virtue_id, story_id, input_sha, rubric_sha):
        key = cell_key(virtue_id, story_id, input_sha, rubric_sha)
        with self.lock:
            row = self.db.execute('SELECT data FROM labels WHERE cell_key=?', (key,)).fetchone()
        return None if row is None else json.loads(row[0])

    def full_owner(self, story_id, input_sha, virtue_id):
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO full_feedback(story_id,input_sha,owner_virtue) VALUES (?,?,?)',
                            (story_id, input_sha, virtue_id))
            owner, complete = self.db.execute('SELECT owner_virtue,complete FROM full_feedback WHERE story_id=? AND input_sha=?',
                                             (story_id, input_sha)).fetchone()
        return owner == virtue_id and not complete

    def register_request(self, packet_key, packet_path, virtue_id):
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO requests(packet_key,packet_path,stage_path,virtue_id) VALUES (?,?,?,?)',
                            (packet_key, str(packet_path), str(Path(packet_path).parent.parent), virtue_id))
            saved = self.db.execute('SELECT packet_path FROM requests WHERE packet_key=?', (packet_key,)).fetchone()
            assert saved == (str(packet_path),), 'Request path changed'

    def pending_requests(self, virtue_id=None):
        condition, params = ('', ()) if virtue_id is None else (' AND virtue_id=?', (virtue_id,))
        for _, path in self._pages('SELECT ordinal,packet_path FROM requests WHERE complete=0' + condition +
                                   ' AND ordinal>? ORDER BY ordinal LIMIT 64', params, after=0):
            yield Path(path)

    def pending_stages(self):
        for _, path in self._pages('SELECT MIN(ordinal),stage_path FROM requests WHERE complete=0 GROUP BY stage_path'
                                   ' HAVING MIN(ordinal)>? ORDER BY MIN(ordinal) LIMIT 64', after=0):
            yield Path(path)

    def stage_requests(self, stage, *, pending=False):
        for _, path in self._pages('SELECT ordinal,packet_path FROM requests WHERE stage_path=?' +
                                   (' AND complete=0' if pending else '') +
                                   ' AND ordinal>? ORDER BY ordinal LIMIT 64', (str(stage),), after=0):
            yield Path(path)

    def commit_request(self, packet_key, cells, full_reviews, native_path):
        with self.lock, self.db:
            for virtue_id, story_id, input_sha, rubric_sha, value in cells:
                key = cell_key(virtue_id, story_id, input_sha, rubric_sha)
                data = json.dumps(value, ensure_ascii=False)
                existing = self.db.execute('SELECT data FROM labels WHERE cell_key=?', (key,)).fetchone()
                if existing is not None:
                    assert existing == (data,), 'Completed cell changed'
                else:
                    self.db.execute('INSERT INTO labels VALUES (?,?,?,?,?,?)',
                                    (key, virtue_id, story_id, input_sha, rubric_sha, data))
            for story_id, input_sha, virtue_id in full_reviews:
                changed = self.db.execute('UPDATE full_feedback SET complete=1,native_path=? WHERE story_id=? AND input_sha=? AND owner_virtue=?',
                                          (str(native_path), story_id, input_sha, virtue_id)).rowcount
                assert changed == 1, 'Full-feedback owner changed'
            changed = self.db.execute('UPDATE requests SET complete=1 WHERE packet_key=?', (packet_key,)).rowcount
            assert changed == 1, 'Request was not registered'

    def record_receipt(self, response_id, request_key, cost_usd, path):
        pico = Decimal(str(cost_usd)) * 10**12
        assert pico.is_finite() and pico >= 0 and pico == pico.to_integral_value(), 'Unexpected receipt precision'
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO receipts VALUES (?,?,?,?,?)',
                            (response_id, request_key, str(cost_usd), str(path), int(pico)))
            row = self.db.execute('SELECT request_key,cost_usd,receipt_path FROM receipts WHERE response_id=?', (response_id,)).fetchone()
            assert row == (request_key, str(cost_usd), str(path)), 'Billing identity changed'

    def total_cost(self):
        with self.lock:
            total = self.db.execute('SELECT COALESCE(SUM(cost_pico),0) FROM receipts').fetchone()[0]
        return Decimal(total) / 10**12

    def receipt_rows(self):
        for row in self._pages('SELECT response_id,request_key,cost_usd,receipt_path FROM receipts'
                               ' WHERE response_id>? ORDER BY response_id LIMIT 64', after=''):
            yield dict(zip(('response_id', 'request_key', 'cost_usd', 'receipt_path'), row, strict=True))
