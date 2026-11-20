import sqlite3
from contextlib import contextmanager


def open_source(path):
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute('PRAGMA foreign_keys = ON')
        db.row_factory = sqlite3.Row
        return db
    except BaseException:
        db.close()
        raise


@contextmanager
def source_write(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise


def require_name(value, field):
    if type(value) is not str or not value.strip():
        raise ValueError(f'{field} must be nonblank text')
    value.encode('utf-8')
    return value


def require_id(value, field):
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise ValueError(f'{field} must be a positive SQLite integer')
    return value


def initialize_intake(db):
    with source_write(db):
        db.execute('''CREATE TABLE IF NOT EXISTS outlets (
            plant TEXT NOT NULL,
            outlet TEXT NOT NULL,
            PRIMARY KEY (plant, outlet)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS batches (
            plant TEXT NOT NULL,
            day TEXT NOT NULL,
            batch TEXT NOT NULL,
            PRIMARY KEY (plant, day, batch)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS revisions (
            revision_id INTEGER PRIMARY KEY,
            plant TEXT NOT NULL,
            day TEXT NOT NULL,
            batch TEXT NOT NULL,
            parent_id INTEGER REFERENCES revisions(revision_id),
            FOREIGN KEY (plant, day, batch) REFERENCES batches(plant, day, batch)
        )''')
        db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS revision_root
            ON revisions (plant, day, batch) WHERE parent_id IS NULL''')
        db.execute('''CREATE TABLE IF NOT EXISTS revision_rows (
            revision_id INTEGER NOT NULL REFERENCES revisions(revision_id),
            ordinal INTEGER NOT NULL CHECK (ordinal > 0),
            outlet TEXT NOT NULL,
            ml TEXT NOT NULL,
            PRIMARY KEY (revision_id, ordinal)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS shared_revisions (
            revision_id INTEGER PRIMARY KEY REFERENCES revisions(revision_id),
            slot_count INTEGER NOT NULL CHECK (slot_count >= 0)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS shared_slots (
            revision_id INTEGER NOT NULL REFERENCES shared_revisions(revision_id),
            ordinal INTEGER NOT NULL CHECK (ordinal > 0),
            ml TEXT NOT NULL,
            PRIMARY KEY (revision_id, ordinal)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS shared_slot_outlets (
            revision_id INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            outlet TEXT NOT NULL,
            PRIMARY KEY (revision_id, ordinal, outlet),
            FOREIGN KEY (revision_id, ordinal) REFERENCES shared_slots(revision_id, ordinal)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS shared_bounds (
            revision_id INTEGER NOT NULL REFERENCES shared_revisions(revision_id),
            outlet TEXT NOT NULL,
            min_ml TEXT NOT NULL,
            max_ml TEXT NOT NULL,
            PRIMARY KEY (revision_id, outlet)
        )''')
