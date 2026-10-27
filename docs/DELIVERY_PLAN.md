# AfterFix v1 delivery plan

Lead decision2026-10-27 following the owner's request to finalize features and use multiple languages. This plan defines work; it does not claim the planned features are implemented. Meta models generate product code/tests; Codex specifies, reviews, executes bounded checks and records actual provenance/results. One implementation request at a time, shared promotional-credit gates unchanged.

## Product promise

After a duplicate-processing bug has been fixed in code, show which persisted inventory records remain wrong, why a particular repair is supported, what will change, and whether valid later activity survives. A human approves a specific snapshot and separately applies the repair. Unsupported evidence stops at needs-review.

The demo's dramatic moment is a computed100 ->110 recovery that keeps a legitimate20-unit restock, backed by citations and actual verification. Held-out cases must use other quantities and IDs. Meta investigates evidence; deterministic code establishes repair quantities/invariants. Do not present a hand-authored scenario explanation as live AI.

## Ordered implementation gates

### A. Access control and bounded runtime setup

Deliver minimal authenticated access, server-side viewer/operator permissions, actor-bound approval/apply audit records, restricted reset and request/concurrent investigation limits. Keep local bootstrap simple, with no signup, billing, external identity platform or credentials in browser source. A viewer may inspect authorized demo records and reports but cannot investigate/spend, reset, approve or apply. An operator may perform those actions within the authorized demo scope. Local setup must be reproducible without the owner's adjacent private control directory; the actual owner's coding/runtime shared budget remains enforced while developing. No unguarded provider fallback.

Acceptance: unauthenticated protected requests denied; viewer writes/investigation denied; authorized operator can complete the existing flow; approvals cannot be borrowed across actor/scope/proposal/snapshot; reset restriction holds; concurrent/oversized requests are bounded; credential leakage/authorization failure regressions pass. Ensure canonical local browser flow still works with authentication. This closes the concrete unauthenticated200 finding in SECURITY_REVIEW.md; do not mark it resolved based on source generation alone.

### B. Bounded Rust verification module

Use the installed Rust/Cargo toolchain (paths located; successful toolchain build not yet established). Define protocol version1 with a target product/current recorded stock, normalized ordered event records and proposed reversal/credit data. Numbers use checked signed64-bit arithmetic; reject booleans, overflow, duplicate IDs, unknown kinds, missing opening history, malformed repair references, cross-product data and inconsistent same-order quantities. Keep protocol limits aligned with the bounded investigator scope (at most200 product events, bounded strings/input/output). No file/network/database access supplied by the caller.

First deliver standalone crate, protocol documentation and meaningful tests. Compare totals/reversal sets/abstention to independent constructed oracles and accepted Python behavior. Do not simply duplicate fixtures that assert hardcoded110. Then add reviewed Python subprocess adapter: fixed built executable, shell disabled, bounded stdin/stdout, timeout, protocol validation, no credentials/paths in prompts or results. Expose real verifier version/check results in preview/report. Integrate as an additional agreement gate for approval and current-state apply; all current transaction/idempotency/preservation checks remain. Disagreement or unavailable verification blocks writes when enabled. No microservice and no database migration.

Acceptance: Cargo tests and existing Python suite pass; malformed/overflow and protocol failure cases fail closed; representative Python/Rust differential cases agree or safely reject; verified canonical browser flow passes with Rust actually invoked. Performance claims require measurements on specified inputs; code share is not a benchmark.

### C. Complete case report and recovery state

Include actual incident/patch, evidence records and IDs, model hypothesis/observations/inferences/uncertainties, model/call/token estimates, snapshot/proposal, preview checks, authenticated approval/apply audit and final verification. Keep before/approval and after snapshots labelled. Add Rust result only when it actually ran. Export stable versioned JSON and a concise human-readable on-screen summary. Do not export credentials, session tokens, private paths or opaque model reasoning.

After success label the repair as applied and verified; retain incident history and the original preview rather than suggesting the duplicate is still waiting to be reversed. A failed check must show unverified/review-required, never success. Make session refresh/reload limitations clear or restore state from authorized backend records; do not silently spend on re-investigation.

Acceptance: downloaded report cross-checks actual saved events/audit/results and can explain the case without chat context; no fabricated metrics, stale success banners or secrets. The failed/needs-review export must also preserve actual reasons when available.

### D. Held-out evaluations and browser failure checks

Freeze a small evaluation matrix before running it; do not tune expected results after observing outputs. Include canonical recovery, altered opening/order/restock quantities, multiple extra deliveries, valid later orders/restocks on target and unrelated products, clean/no-repair ledger, missing opening evidence, contradictory quantities, stale snapshot and adversarial log/patch text. Use at least6 distinct live cases under reviewed per-run and shared credit caps, with failures/abstentions retained. Offline tests cover broader combinations; do not label offline replays live. Save exact setup/oracle, real outputs, correct proposal or safe abstention, unintended-write count and usage.

Browser checks: authenticated desktop complete flow, citation/patch keyboard dialogs, approval and apply separation, stable retry key, actual stale409 invalidation, unavailable model/budget failure, viewer/unauthenticated permissions, report download and mobile layout. An independent backend/DB check confirms actual writes and preserved records. No repeated paid request merely to polish model wording.

Acceptance: report all outcomes; zero unauthorized/unapproved/double repairs in evaluated cases; valid later records preserved; unsupported cases cannot reach apply. A live failure remains visible and is fixed through a focused reviewed task when justified. No guarantee of100% reliability outside evaluated inputs.

### E. Release candidate and demo package

Verify Python/JS/Rust dependency advisories and lock/pin inputs, exact-value credential scan, no saved secrets/private environment, clean-checkout build/setup and limited synthetic seed. Document budget/credential setup, supported environment and known limitations. Build static frontend for hosting preparation; do not expose Vite dev server. Rehearse a three-minute story: fixed code/still-wrong data -> real investigation/citations -> computed preview/Rust checks -> explicit approval/apply -> verified preserved restock/report.

Prepare public-source README, architecture/provenance, evaluation results, screenshots, video script and deployment/submission checklist. Public repository creation/push, deployment, video publication and contest submission remain separate concrete actions; none is authorized by this planning document. Preserve submission buffer: stop initiating work by2026-11-05 02:00 Asia/Dhaka. Host/tool availability and credit freshness constrain recurring work.

## Change discipline

Keep each Meta task small and reviewable, stage files before executing, run checks appropriate to that change and checkpoint milestones. Prioritize the above gates over new feature ideas. If Rust integration changes existing behavior, surface the mismatch and resolve evidence/semantics; do not hide it behind fallback or weaken tests. Keep exact phase, commands/results, model usage and next action in STATE.md. The40/35/20/5 allocation is a planning guide only; working reliability and a clear three-minute demo are the acceptance criteria.
