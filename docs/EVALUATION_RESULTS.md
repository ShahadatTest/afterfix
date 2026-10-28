# First held-out live evaluation

On2026-10-28, the frozen7-case synthetic matrix produced **5 passes and2 failures** with actual Meta Spark1.3 responses in all7 distinct cases. This is the retained first-attempt result; later code changes do not replace it.

| Case | Expected | Actual outcome | Result |
| --- | --- | --- | --- |
| H01 varied quantities | Repair7, final145 | One approved repair; Rust and preservation verification passed145 | Pass |
| H02 multiple extra deductions | Repair39, final259 | Invalid/incomplete response; zero approvals/repairs | Fail |
| H03 preserve later activity | Repair6, final87 | One approved repair; Rust and preservation verification passed87 | Pass |
| H04 clean replay | Valid semantic abstention | Invalid/incomplete response; zero approvals/repairs | Fail |
| H05 missing opening history | Abstain | Valid model needs-review; zero approvals/repairs | Pass |
| H06 conflicting quantities | Abstain | Valid model needs-review; zero approvals/repairs | Pass |
| H07 adversarial event note | Repair9, final93 | One approved repair; Rust and preservation verification passed93 | Pass |

The batch made21 actual provider calls:23,499 input and9,163 output tokens, locally estimated$0.0683165 using configured rates. All21 calls settled with known usage; no retries, unknown reservations or simulated responses. Source/Rust hashes matched the reviewed launch snapshot, and the manifest's canonical digest remained `bca5a6c003d03ef9fedf4eeedd2624ecb13f4f77f2ba3070535db5092aceca51`.

Both failed final requests consumed exactly1200 output tokens, the configured final allowance. This is consistent with output exhaustion; provider `incomplete_details` were not retained, so the exact upstream cause is not asserted. The clean case does not pass merely because an incomplete response caused no write. Partial model output was never accepted or resumed.

Original per-case JSON and summary are retained under [heldout-live-v1](evaluations/heldout-live-v1/summary.json), with [independent lead review and ledger reconciliation](evaluations/heldout-live-v1/lead-review.json). The next development correction may use the existing2048 client allowance and a fixed `model_output_limit` category, with fake-response regression tests; it will not rerun or relabel this first benchmark.

This small synthetic function-level matrix is not production, authentication, browser, mobile or universal-recovery validation. Actual browser desktop evidence is documented separately. Mobile/delayed-failure/stale UI checks and release preparation remain pending. No contest-score guarantee is made.
