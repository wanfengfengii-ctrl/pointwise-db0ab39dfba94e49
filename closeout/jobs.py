from closeout.grouping import group_by_outlet
from closeout.results import find_result_for_revision, store_result
from closeout.totals import calculate_totals
from intake.revisions import get_revision
from intake.source_store import source_write


def close_batch(db, revision_id):
    with source_write(db):
        existing = find_result_for_revision(db, revision_id)
        if existing is not None:
            return existing
        revision = get_revision(db, revision_id)
        if revision is None:
            raise KeyError(f'unknown revision {revision_id}')
        totals = calculate_totals(group_by_outlet(revision.rows))
        return store_result(db, revision, totals)
