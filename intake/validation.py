from collections.abc import Mapping, Sequence

from intake.outlets import find_outlet
from intake.revisions import RevisionRow, get_revision
from intake.source_store import require_name


def validate_batch_rows(db, plant, rows):
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise ValueError('rows must be an ordered sequence')
    validated = []
    for ordinal, item in enumerate(rows, start=1):
        if not isinstance(item, Mapping) or set(item) != {'outlet', 'ml'}:
            raise ValueError(f'row {ordinal} requires outlet and ml')
        outlet = require_name(item['outlet'], 'outlet')
        ml = item['ml']
        if type(ml) is not int:
            raise ValueError(f'row {ordinal} ml must be an integer')
        if not find_outlet(db, plant, outlet):
            raise ValueError(f'row {ordinal} references an unknown outlet')
        validated.append(RevisionRow(ordinal, outlet, ml))
    return tuple(validated)


def validate_parent_revision(db, plant, day, batch, parent_id):
    if parent_id is None:
        existing = db.execute('''SELECT 1 FROM batches
            WHERE plant = ? AND day = ? AND batch = ?''', (plant, day, batch)).fetchone()
        if existing is not None:
            raise ValueError('an existing batch requires a parent revision')
        return
    parent = get_revision(db, parent_id)
    if parent is None or (parent.plant, parent.day, parent.batch) != (plant, day, batch):
        raise ValueError('parent revision must belong to the same batch')
