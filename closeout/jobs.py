from closeout.grouping import group_by_outlet
from closeout.results import (find_result_for_revision, store_result,
                              store_shared_result)
from closeout.shared import solve_allocation
from closeout.totals import calculate_totals
from intake.revisions import BatchRevision, get_revision, get_shared_revision, is_shared_revision
from intake.source_store import source_write


def _freeze_shared(db, revision_id):
    shared_revision = get_shared_revision(db, revision_id)
    shared = solve_allocation(shared_revision.slots, shared_revision.bounds)
    revision = BatchRevision(shared_revision.revision_id, shared_revision.plant,
                             shared_revision.day, shared_revision.batch,
                             shared_revision.parent_id, shared.rows)
    return store_shared_result(db, revision, shared)


def close_batch(db, revision_id):
    with source_write(db):
        existing = find_result_for_revision(db, revision_id)
        if existing is not None:
            return existing
        if is_shared_revision(db, revision_id):
            return _freeze_shared(db, revision_id)
        revision = get_revision(db, revision_id)
        if revision is None:
            raise KeyError(f'unknown revision {revision_id}')
        totals = calculate_totals(group_by_outlet(revision.rows))
        return store_result(db, revision, totals)
