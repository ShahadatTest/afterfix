# Held-out inventory evaluation protocol

Status: tooling only. Frozen 7-case matrix `docs/evaluations/heldout-inventory-v1.json`: **LIVE NOT RUN**. No live score or pass-rate claim. Lead decides any live execution.

## Integrity and isolation
Digest is SHA-256 of canonical JSON (`sort_keys`, compact separators, ASCII). Manifest is read with a 256 KiB bound, paired `.sha256`, strict integer/version and ID `fullmatch` checks before any case, transport, or output. Exactly the declared cases or a bounded `--case` subset run. Investigator receives only incident/patch/requested events; oracle/expected/marker fields are local scoring data. Each case uses fresh in-memory SQLite, actual buggy/fixed/restock handlers, independent pre-state rows, one investigation, and one `record_case` attempt. A recording return other than a nonempty `CASE-...` string, or an exception, is failure and never causes re-investigation.

## Scoring
Before investigation the tool captures `store.fingerprint(conn, product_id)`. Supported cases require exact decision, credit, extra reverse IDs, recorded/corrected stock, and proposal `proposal_id`/`incident_id`/`product_id`/`status`/snapshot exactly bound to that fingerprint (missing `product_id` fails). Preview must have the same nested proposal binding, `snapshot_match is True`, nonempty all-passing simulation and invariant checks, and required Rust `enabled:true`, `status:verified`, version `0.1.0`, same snapshot hash, passing checks, and `proposal_matches:true`. Preview-only default requires zero approvals/repairs.

Unsupported cases pass only for legitimate `model_needs_review` abstention with no actionable proposal, approval, or repair. Operational failures never count as abstention: unknown usage (regardless of decision/category), `model_client_error`, `client_unavailable`, `investigation_error`, guard entry/cleanup errors, and model parse/schema failures fail the case. Unknown actual tokens/cost remain `null`; no real-spend estimate is invented.

Optional `--apply-synthetic` additionally requires approval bound to proposal/snapshot/actor with verified Rust, first apply exactly equal to the stable same-key retry (the saved object still has `idempotent:false`), target final stock, one repair and one approval, and verification passing. All original event rows and unrelated product rows must be identical; the target product may change only by the exact credit stock increase, plus exactly one new repair event. Original preview must be immutable.

## Modes, guard, and stopping
Offline (default) uses a deterministic fake Responses transport through `MetaClient`; outputs are simulated, not live AI or spend, and never count as provider evidence. It loads no `.env` and no guard.

Live requires `--live --confirm-synthetic --runtime-dir EXISTING_ABSOLUTE_DIR`. Per case the tool passes `str(Path(runtime_dir))` to the existing `RuntimeBudgetTransport`, uses `with`-equivalent explicit enter/exit exactly once, and passes only a bounded local counting wrapper around that guard to `MetaClient`. No guard internals, custom HTTP client, endpoint/model override, fallback, retry, or parallelism. Usage/result metadata captured before cleanup is retained if cleanup fails; cleanup failure is operational failure, not swallowed success.

The runner records `live_requested`, attempted client requests, successfully returned guarded provider responses, simulated responses separately, and distinct cases with at least one returned provider response. `live_ai` is true only from actual returned provider responses, not flags; offline/simulated responses never count toward the required six distinct real cases. Any operational failure or unknown usage saves the attempted case and partial summary, then stops remaining cases immediately (`stopped_early` when cases remain). Summary/per-case outputs contain bounded outcome, stop category, failures, and usage only—no raw payloads, paths, secrets, provider bodies, or reasoning.

## Commands (offline examples)
```text
.venv/Scripts/python.exe tools/evaluate_inventory.py --output evaluations/heldout-offline-1
.venv/Scripts/python.exe tools/evaluate_inventory.py --output evaluations/heldout-offline-apply-1 --apply-synthetic
```
`--output` must be a new directory. Exit is nonzero if any attempted case fails or configuration/manifest/guard errors occur; attempted outcomes remain saved.

## Limitations
Small synthetic function-level matrix; not a security certification, production benchmark, auth/route benchmark, or universal recovery claim. Offline results validate wiring/fixtures/scoring/Rust preview only. Live held-out results do not exist yet. Required Rust must be built as documented; missing/disagreeing Rust blocks preview/approval/apply.
