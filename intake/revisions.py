from dataclasses import dataclass
from decimal import Decimal

from intake.source_store import require_id


@dataclass(frozen=True)
class RevisionRow:
    ordinal: int
    outlet: str
    ml: int


@dataclass(frozen=True)
class BatchRevision:
    revision_id: int
    plant: str
    day: str
    batch: str
    parent_id: int | None
    rows: tuple[RevisionRow, ...]


def get_revision(db, revision_id):
    require_id(revision_id, 'revision_id')
    header = db.execute('''SELECT plant, day, batch, parent_id FROM revisions
        WHERE revision_id = ?''', (revision_id,)).fetchone()
    if header is None:
        return None
    records = db.execute('''SELECT ordinal, outlet, ml FROM revision_rows
        WHERE revision_id = ? ORDER BY ordinal''', (revision_id,))
    rows = tuple(RevisionRow(ordinal, outlet, int(Decimal(ml)))
                 for ordinal, outlet, ml in records)
    return BatchRevision(revision_id, *header, rows)


def append_revision(db, plant, day, batch, parent_id, rows):
    """Persist a validated revision inside the caller's source transaction."""
    if parent_id is None:
        db.execute('INSERT INTO batches (plant, day, batch) VALUES (?, ?, ?)',
                   (plant, day, batch))
    cursor = db.execute('''INSERT INTO revisions (plant, day, batch, parent_id)
        VALUES (?, ?, ?, ?)''', (plant, day, batch, parent_id))
    revision_id = cursor.lastrowid
    db.executemany('''INSERT INTO revision_rows (revision_id, ordinal, outlet, ml)
        VALUES (?, ?, ?, ?)''',
        ((revision_id, row.ordinal, row.outlet, str(Decimal(row.ml))) for row in rows))
    return revision_id
