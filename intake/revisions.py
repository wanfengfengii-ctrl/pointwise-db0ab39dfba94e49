import json
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


def insert_revision(db, plant, day, batch, parent_id):
    """Reserve a revision number inside the caller's source transaction."""
    if parent_id is None:
        db.execute('INSERT INTO batches (plant, day, batch) VALUES (?, ?, ?)',
                   (plant, day, batch))
    cursor = db.execute('''INSERT INTO revisions (plant, day, batch, parent_id)
        VALUES (?, ?, ?, ?)''', (plant, day, batch, parent_id))
    return cursor.lastrowid


@dataclass(frozen=True)
class SharedRevision:
    revision_id: int
    plant: str
    day: str
    batch: str
    parent_id: int | None
    slots: tuple['SharedSlot', ...]
    bounds: tuple['OutletBound', ...]


def _shared_slots(db, revision_id):
    from intake.shared import SharedSlot
    records = db.execute('''SELECT ordinal, ml FROM shared_slots
        WHERE revision_id = ? ORDER BY ordinal''', (revision_id,))
    slots = []
    for ordinal, ml in records:
        permits = db.execute('''SELECT outlet FROM shared_slot_outlets
            WHERE revision_id = ? AND ordinal = ? ORDER BY outlet''', (revision_id, ordinal))
        slots.append(SharedSlot(ordinal, int(Decimal(ml)), tuple(row[0] for row in permits)))
    return tuple(slots)


def _shared_bounds(db, revision_id):
    from intake.shared import OutletBound
    records = db.execute('''SELECT outlet, min_ml, max_ml FROM shared_bounds
        WHERE revision_id = ? ORDER BY outlet''', (revision_id,))
    return tuple(OutletBound(outlet, int(Decimal(lo)), int(Decimal(hi)))
                 for outlet, lo, hi in records)


def is_shared_revision(db, revision_id):
    return db.execute('SELECT 1 FROM shared_revisions WHERE revision_id = ?',
                      (revision_id,)).fetchone() is not None


def get_shared_revision(db, revision_id):
    require_id(revision_id, 'revision_id')
    header = db.execute('''SELECT s.revision_id, r.plant, r.day, r.batch, r.parent_id
        FROM shared_revisions AS s JOIN revisions AS r USING (revision_id)
        WHERE s.revision_id = ?''', (revision_id,)).fetchone()
    if header is None:
        return None
    return SharedRevision(*header, _shared_slots(db, revision_id),
                          _shared_bounds(db, revision_id))


def append_shared_revision(db, plant, day, batch, parent_id, slots, bounds):
    """Persist a validated shared revision inside the caller's source transaction."""
    revision_id = insert_revision(db, plant, day, batch, parent_id)
    db.execute('INSERT INTO shared_revisions (revision_id, slot_count) VALUES (?, ?)',
               (revision_id, len(slots)))
    db.executemany('''INSERT INTO shared_slots (revision_id, ordinal, ml) VALUES (?, ?, ?)''',
                   ((revision_id, slot.ordinal, str(Decimal(slot.ml))) for slot in slots))
    db.executemany('''INSERT INTO shared_slot_outlets (revision_id, ordinal, outlet)
        VALUES (?, ?, ?)''',
        ((revision_id, slot.ordinal, outlet) for slot in slots for outlet in slot.outlets))
    db.executemany('''INSERT INTO shared_bounds (revision_id, outlet, min_ml, max_ml)
        VALUES (?, ?, ?, ?)''',
        ((revision_id, bound.outlet, str(Decimal(bound.min_ml)), str(Decimal(bound.max_ml)))
         for bound in bounds))
    return revision_id


def append_revision(db, plant, day, batch, parent_id, rows):
    """Persist a validated revision inside the caller's source transaction."""
    revision_id = insert_revision(db, plant, day, batch, parent_id)
    db.executemany('''INSERT INTO revision_rows (revision_id, ordinal, outlet, ml)
        VALUES (?, ?, ?, ?)''',
        ((revision_id, row.ordinal, row.outlet, str(Decimal(row.ml))) for row in rows))
    return revision_id
