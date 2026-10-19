"""Synthetic companion to the 2026-10-19 article. Python 3.8+, SQLite FTS5.
Run: python memory-revalidation.py
This is illustrative code, not CADb source.
"""
import hashlib
import json
import sqlite3
import unittest


def snapshot(rows, policy):
    # Complete candidate snapshot; unique IDs and trusted kind labels assumed.
    ordered = sorted(rows, key=lambda row: row['id'])
    accepted = [row for row in ordered
                if policy == 'v1' or row['kind'] == 'rule']
    payload = {'policy': policy, 'rows': ordered}
    generation = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(',', ':')
    ).encode()).hexdigest()
    return accepted, generation


def install(db, rows, policy, fail=False):
    accepted, generation = snapshot(rows, policy)
    # Schema is initialized separately, outside the publication transaction.
    with db:
        db.execute('DELETE FROM active')
        db.executemany('INSERT INTO active VALUES (?, ?)',
                       [(row['id'], row['text']) for row in accepted])
        if fail:
            raise RuntimeError('simulated interruption')
        db.execute("INSERT INTO search(search) VALUES ('rebuild')")
        db.execute('DELETE FROM manifest')
        db.execute('INSERT INTO manifest VALUES (?)', (generation,))
    return generation


def connect():
    db = sqlite3.connect(':memory:')
    db.executescript('''
        CREATE TABLE active(id INTEGER PRIMARY KEY, text TEXT NOT NULL);
        CREATE VIRTUAL TABLE search USING fts5(
            text, content='active', content_rowid='id');
        CREATE TABLE manifest(generation TEXT NOT NULL);
    ''')
    return db


def hits(db, query):
    return [row[0] for row in db.execute(
        'SELECT rowid FROM search WHERE search MATCH ? ORDER BY rowid',
        (query,))]


ROWS = [
    {'id': 1, 'kind': 'rule', 'text': 'Prefer explicit timezone settings.'},
    {'id': 2, 'kind': 'observation', 'text': 'The queue contains seven tasks.'},
]


class RevalidationTests(unittest.TestCase):
    def setUp(self):
        self.db = connect()
        self.addCleanup(self.db.close)

    def test_same_input_is_idempotent(self):
        first = install(self.db, ROWS, 'v1')
        second = install(self.db, ROWS, 'v1')
        self.assertEqual(first, second)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM active').fetchone()[0], 2)
        self.assertEqual(hits(self.db, 'queue'), [2])

    def test_policy_change_removes_old_search_hit(self):
        old = install(self.db, ROWS, 'v1')
        self.assertEqual(hits(self.db, 'queue'), [2])
        new = install(self.db, ROWS, 'v2')
        self.assertNotEqual(old, new)
        self.assertEqual(hits(self.db, 'queue'), [])
        self.assertEqual(hits(self.db, 'timezone'), [1])
        self.assertEqual(self.db.execute('SELECT generation FROM manifest').fetchone()[0], new)

    def test_interruption_preserves_previous_generation(self):
        old = install(self.db, ROWS, 'v1')
        with self.assertRaises(RuntimeError):
            install(self.db, ROWS, 'v2', fail=True)
        self.assertEqual(hits(self.db, 'queue'), [2])
        self.assertEqual(self.db.execute('SELECT generation FROM manifest').fetchone()[0], old)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM active').fetchone()[0], 2)

    def test_same_count_different_content_changes_generation(self):
        original = install(self.db, ROWS, 'v2')
        changed = [dict(ROWS[0], text='Prefer explicit locale settings.'), ROWS[1]]
        updated = install(self.db, changed, 'v2')
        self.assertNotEqual(original, updated)
        self.assertEqual(hits(self.db, 'timezone'), [])
        self.assertEqual(hits(self.db, 'locale'), [1])

    def test_empty_projection_clears_search(self):
        install(self.db, ROWS, 'v1')
        install(self.db, [], 'v2')
        self.assertEqual(hits(self.db, 'queue'), [])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM active').fetchone()[0], 0)

    def test_input_order_does_not_change_generation(self):
        self.assertEqual(snapshot(ROWS, 'v2'), snapshot(list(reversed(ROWS)), 'v2'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
