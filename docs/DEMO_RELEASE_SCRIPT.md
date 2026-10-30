# Release demonstration: actual live tests

This is an edited narrated demonstration assembled from screenshots of actual release-smoke81 browser actions, not a continuous screen recording. Generic installed Microsoft David text-to-speech is used. Inventory is synthetic. The first provider failure remains visible; the frozen seven-case benchmark remains5/7 and is not rerun.

## 0:00 to 0:10

AfterFix: recover the data left behind.

Fixing a software bug does not fix the data it already damaged. AfterFix investigates the evidence, previews a bounded repair, and keeps the operator in control.

## 0:10 to 0:25

The processing bug is fixed. The stored damage remains.

Here the processing code now rejects duplicate delivery of the same business order. But the old duplicate deduction remains in stored inventory. The patch is evidence to inspect, never permission for the model to write.

## 0:25 to 0:35

Duplicate deduction, then a legitimate restock.

One ten-unit order was deducted twice. A legitimate twenty-unit restock happened later. Recovery must reverse only the extra deduction and preserve valid activity.

## 0:35 to 0:45

A provider failure stops safely. It is retained in the report.

The first live provider attempt failed. AfterFix stopped at needs review with no proposal and no write. We retain that failure instead of hiding it or silently retrying.

## 0:45 to 0:57

Incomplete opening history blocks a repair.

In a separate live case, opening history was missing. The model offered a hypothesis, but deterministic validation rejected an unsupported repair. Model claims alone cannot authorize a database change.

## 0:57 to 1:15

Live Meta investigation produces an evidence-linked proposal.

For this fresh case, real Meta Spark investigated the patch and event records through bounded read-only tools. It cited the duplicate deduction. The original computed preview is one hundred to one hundred ten. No arbitrary model-generated SQL is executed.

## 1:15 to 1:25

Python and the independent Rust verifier must agree.

An independent Rust verifier checks the ledger, quantities and reversal references. It must agree with Python. Missing verification or disagreement blocks writes.

## 1:25 to 1:37

Review, approval and apply are separate actions.

The operator reviews the preview and explicitly approves this proposal and snapshot. Approval is separate from applying the repair. The database has not been changed merely because approval was recorded.

## 1:37 to 1:55

A later valid restock invalidates the earlier approval.

Now a legitimate one-unit restock changes the data. Recorded stock becomes one hundred one. AfterFix removes the stale preview and approval from the active workflow. An attempt to apply the old approval returns a conflict and writes no repair.

## 1:55 to 2:10

A fresh investigation computes 101 to 111.

The operator explicitly investigates the new snapshot. Real Meta cites the updated evidence, and the new computed preview is one hundred one to one hundred eleven. Both legitimate restocks remain preserved. The target is computed, not hardcoded.

## 2:10 to 2:30

Verified: one repair; both valid restocks preserved.

After a new explicit approval and separate apply, verification reports stock one hundred eleven. The independent database check confirms exactly one repair, both restocks of twenty and one, and the unrelated product unchanged at forty-five. Transaction, snapshot and idempotency checks remain enforced.

## 2:30 to 2:40

A durable, read-only case report explains the result.

Saved reports retain evidence, claims, snapshots, approvals and verification. They reload read-only and never restore permission to apply.

## 2:40 to 3:00

Synthetic inventory only. Failures and limits stay visible.

This edited demonstration uses screenshots captured from actual live release tests, not a continuous screen recording. Inventory is synthetic. The original frozen seven-case benchmark remains five passes and two failures. AfterFix is not an arbitrary production SQL agent or universal rollback system. Unsupported evidence stops for review.

Actual separate release results: first service failure stopped safely; missing-opening model draft rejected by deterministic validation; stale approved snapshot rejected409; explicit new live investigation and approval/apply verified111 with both restocks preserved. Four investigations/10providerattempts,9confirmedusage, oneunknown fullyreserved; conservative$0.0357975. See evaluations/release-live-2026-10-30.json. Video binary is distributed separately from source.
