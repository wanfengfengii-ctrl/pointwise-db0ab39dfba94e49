import sqlite3
from contextlib import contextmanager


def open_receiver(path, *, factory=sqlite3.Connection):
    db = sqlite3.connect(path, isolation_level=None, factory=factory)
    try:
        db.execute('PRAGMA foreign_keys = ON')
        db.row_factory = sqlite3.Row
        return db
    except BaseException:
        db.close()
        raise


@contextmanager
def _transaction(db, begin):
    db.execute(begin)
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise


@contextmanager
def receiver_read(db):
    if db.in_transaction:
        yield db
        return
    with _transaction(db, 'BEGIN'):
        yield db


@contextmanager
def receiver_write(db):
    with _transaction(db, 'BEGIN IMMEDIATE'):
        yield db


def initialize_receiver(db):
    with receiver_write(db):
        db.execute('''CREATE TABLE IF NOT EXISTS staged_reports (
            stage_id INTEGER PRIMARY KEY,
            report_id TEXT NOT NULL UNIQUE,
            original_id TEXT NOT NULL,
            body BLOB NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS posted_reports (
            report_id TEXT PRIMARY KEY NOT NULL REFERENCES staged_reports(report_id),
            row_count INTEGER NOT NULL CHECK (row_count >= 0),
            ml TEXT NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS ledger_rows (
            report_id TEXT NOT NULL REFERENCES posted_reports(report_id),
            ordinal INTEGER NOT NULL CHECK (ordinal > 0),
            outlet TEXT NOT NULL,
            ml TEXT NOT NULL,
            PRIMARY KEY (report_id, ordinal)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS ledger_outlet_totals (
            report_id TEXT NOT NULL REFERENCES posted_reports(report_id),
            outlet TEXT NOT NULL,
            row_count INTEGER NOT NULL CHECK (row_count > 0),
            ml TEXT NOT NULL,
            PRIMARY KEY (report_id, outlet)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS receipts (
            receipt_id INTEGER PRIMARY KEY,
            report_id TEXT NOT NULL UNIQUE REFERENCES posted_reports(report_id),
            original_id TEXT NOT NULL
        )''')
