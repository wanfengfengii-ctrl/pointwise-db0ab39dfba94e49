from intake.revisions import append_revision
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
