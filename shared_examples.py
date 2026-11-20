"""Show what ordinary V1 reports can establish about the shared-meter sample."""
import json
from contextlib import closing
from pathlib import Path

from closeout.jobs import close_batch
from closeout.results import initialize_closeout
from dispatch.issuance import issue_report
from dispatch.outbox import initialize_outbox
from intake.batches import accept_batch
from intake.outlets import register_outlet
from intake.source_store import initialize_intake, open_source
from posting.ledger import post_report
from posting.receiver_store import initialize_receiver, open_receiver
from posting.staging import stage_report
from review.reports import audit_report


def main():
    sample = json.loads((Path(__file__).parent / "samples" / "shared_day.json").read_text(encoding="utf-8"))
    plant, day = sample["plant"], sample["day"]
    case = sample["cases"]["forced"]
    projected = [{"outlet": outlet, "ml": ml}
                 for _, _, items in case["expected"] for _, outlet, ml in items]
    orders = {"ordinary-correct-order": projected,
              "ordinary-swapped-order": projected[::-1]}
    envelopes = []
    with closing(open_source(":memory:")) as source:
        initialize_intake(source)
        initialize_closeout(source)
        initialize_outbox(source)
        for outlet in sample["outlets"]:
            register_outlet(source, plant, outlet)
        for report_id, rows in orders.items():
            revision_id = accept_batch(source, plant, day, report_id, rows)
            result = close_batch(source, revision_id)
            envelopes.append(issue_report(source, report_id, result.result_id).envelope)

    with closing(open_receiver(":memory:")) as receiver:
        initialize_receiver(receiver)
        for envelope in envelopes:
            report_id = envelope.report_id
            posted = post_report(receiver, stage_report(receiver, envelope))
            audit = audit_report(receiver, report_id)
            expected = [(rank, row["outlet"], row["ml"])
                        for rank, row in enumerate(orders[report_id], 1)]
            rows = [(row.ordinal, row.outlet, row.ml) for row in audit.ledger_rows]
            if (rows != expected or audit.discrepancies or audit.receipt != posted.receipt
                    or audit.ledger_totals.outlets != (("A", 1, 5), ("B", 1, 5))
                    or (audit.ledger_totals.row_count, audit.ledger_totals.ml) != (2, 10)):
                raise AssertionError(f"unexpected V1 posting or audit for {report_id}")
            print(json.dumps({"report": report_id, "rows": rows, "total_ml": 10,
                              "discrepancies": len(audit.discrepancies)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
