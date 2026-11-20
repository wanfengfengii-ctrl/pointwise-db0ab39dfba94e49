from intake.revisions import append_revision, append_shared_revision
from intake.shared import validate_bounds, validate_slots
from intake.source_store import require_name, source_write
from intake.validation import validate_batch_rows, validate_parent_revision


def accept_batch(db, plant, day, batch, rows, *, parent_id=None):
    require_name(plant, 'plant')
    require_name(day, 'day')
    require_name(batch, 'batch')
    with source_write(db):
        validate_parent_revision(db, plant, day, batch, parent_id)
        ordered = validate_batch_rows(db, plant, rows)
        return append_revision(db, plant, day, batch, parent_id, ordered)


def accept_shared_batch(db, plant, day, batch, slots, bounds, parent_id=None):
    require_name(plant, 'plant')
    require_name(day, 'day')
    require_name(batch, 'batch')
    with source_write(db):
        validate_parent_revision(db, plant, day, batch, parent_id)
        ordered = validate_slots(db, plant, slots)
        daily = validate_bounds(db, plant, ordered, bounds)
        return append_shared_revision(db, plant, day, batch, parent_id, ordered, daily)
