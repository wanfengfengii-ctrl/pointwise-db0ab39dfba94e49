from dataclasses import dataclass
from decimal import Decimal

from closeout.totals import CloseoutTotals, OutletTotal
from intake.revisions import BatchRevision, RevisionRow
from intake.source_store import require_id, source_write


@dataclass(frozen=True)
class CloseoutResult:
    result_id: int
    revision: BatchRevision
    totals: CloseoutTotals


def initialize_closeout(db):
    with source_write(db):
        db.execute('''CREATE TABLE IF NOT EXISTS closeouts (
            result_id INTEGER PRIMARY KEY,
            revision_id INTEGER NOT NULL UNIQUE REFERENCES revisions(revision_id),
            row_count INTEGER NOT NULL CHECK (row_count >= 0),
            ml TEXT NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS closeout_rows (
            result_id INTEGER NOT NULL REFERENCES closeouts(result_id),
            ordinal INTEGER NOT NULL CHECK (ordinal > 0),
            outlet TEXT NOT NULL,
            ml TEXT NOT NULL,
            PRIMARY KEY (result_id, ordinal)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS closeout_outlets (
            result_id INTEGER NOT NULL REFERENCES closeouts(result_id),
            outlet TEXT NOT NULL,
            row_count INTEGER NOT NULL CHECK (row_count > 0),
            ml TEXT NOT NULL,
            PRIMARY KEY (result_id, outlet)
        )''')


def store_result(db, revision, totals):
    """Freeze the calculation inside the caller's source transaction."""
    cursor = db.execute('''INSERT INTO closeouts (revision_id, row_count, ml)
        VALUES (?, ?, ?)''', (revision.revision_id, totals.row_count, str(Decimal(totals.ml))))
    result_id = cursor.lastrowid
    db.executemany('''INSERT INTO closeout_rows (result_id, ordinal, outlet, ml)
        VALUES (?, ?, ?, ?)''',
        ((result_id, row.ordinal, row.outlet, str(Decimal(row.ml))) for row in revision.rows))
    db.executemany('''INSERT INTO closeout_outlets (result_id, outlet, row_count, ml)
        VALUES (?, ?, ?, ?)''',
        ((result_id, item.outlet, item.row_count, str(Decimal(item.ml))) for item in totals.outlets))
    return CloseoutResult(result_id, revision, totals)


def get_result(db, result_id):
    require_id(result_id, 'result_id')
    header = db.execute('''SELECT r.revision_id, r.plant, r.day, r.batch, r.parent_id,
            c.row_count, c.ml
        FROM closeouts AS c JOIN revisions AS r ON r.revision_id = c.revision_id
        WHERE c.result_id = ?''', (result_id,)).fetchone()
    if header is None:
        return None
    records = db.execute('''SELECT ordinal, outlet, ml FROM closeout_rows
        WHERE result_id = ? ORDER BY ordinal''', (result_id,))
    rows = tuple(RevisionRow(ordinal, outlet, int(Decimal(ml)))
                 for ordinal, outlet, ml in records)
    revision = BatchRevision(*header[:5], rows)
    records = db.execute('''SELECT outlet, row_count, ml FROM closeout_outlets
        WHERE result_id = ? ORDER BY outlet''', (result_id,))
    outlets = tuple(OutletTotal(outlet, count, int(Decimal(ml)))
                    for outlet, count, ml in records)
    totals = CloseoutTotals(outlets, header['row_count'], int(Decimal(header['ml'])))
    return CloseoutResult(result_id, revision, totals)


def find_result_for_revision(db, revision_id):
    require_id(revision_id, 'revision_id')
    record = db.execute('SELECT result_id FROM closeouts WHERE revision_id = ?',
                        (revision_id,)).fetchone()
    return None if record is None else get_result(db, record['result_id'])
