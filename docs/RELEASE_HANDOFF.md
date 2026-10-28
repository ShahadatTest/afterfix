# AfterFix release handoff

For setup, use the [README](../README.md); for the timed narration, use the [demo script](DEMO_SCRIPT.md). This handoff records what a reviewer can verify now and what remains before external release.

## Problem and three-minute flow

A duplicate-delivery bug deducted one 10-unit order twice: stock moved **100 → 80**. A later legitimate 20-unit restock made it **100**, but fixing the code did not repair the stored value.

1. Sign in as the configured local operator and open the canonical incident, patch, and timeline.
2. Start one explicit live Meta Spark 1.3 investigation. It uses bounded read-only tools, cites observed evidence, and separates observations, inferences, and uncertainties.
3. Review the deterministic preview: reverse only the extra deduction, **100 → 110**, while preserving the 20-unit restock.
4. Confirm the required independent Rust gate, tick the review box, and approve. Approval binds the proposal and snapshot.
5. Apply as a separate action. Python/SQLite owns the transaction, stale-state check, stable retry key, and audit record; React/TypeScript keeps the application token only in memory and enforces the operator/viewer interaction.
6. Verify stock 110 and preservation checks, open the saved case read-only, and download the exact JSON report. Reloading never restores approval or apply state.

## Evidence — keep these categories separate

**Actual live canonical:** an authenticated Chrome recovery with real Spark 1.3 completed 100 → 110 with one approval/repair and 10 checks: [canonical browser evidence](evaluations/browser-rust-recovery-2026-10-28.json).

**Immutable held-out live benchmark:** the frozen seven-case set remains **5/7 passed, 2/7 failed**; H02/H04 are retained failures. It was not rerun or relabelled, and no live improvement is claimed from the later 2048 output-cap change: [evaluation results](EVALUATION_RESULTS.md) and [model provenance](MODEL_PROVENANCE.md).

**Offline or simulated:** the broad Python/Rust suites, offline held-out apply checks, and desktop Chrome failure/session checks are not live-provider evidence. The latter covered delayed old-report discard after 7.66 seconds, a simulated pre-provider budget denial, a simulated dependency 503, and dialog keyboard/focus behaviour at desktop 1280 × 585; they are not an accessibility certification: [browser session/failure evidence](evaluations/browser-session-failure-2026-10-28.json).

**Bootstrap/dependencies:** the exact README bootstrap was actually run on the project `.venv`, changing only pip 25.0.1 → 26.2.2 among 24 PyPI distributions; `pip check` passed. A new 103-triple OSV query completed with 0 indexed matches and no partial counts. The original 12-ID/six-issue snapshot remains unchanged: [dependency results](DEPENDENCY_RESULTS.md) and [v2 evidence](evaluations/bootstrap-verified-v2/lead-review.json). Application requirements were not reinstalled, and no fresh-checkout, backend 403, Rust 32, or browser suite was rerun for this installer-only change. Zero matches is not a security certificate. See also the [security review](SECURITY_REVIEW.md) and [release checklist](RELEASE_CHECKLIST.md).

The installer validation used zero Meta/runtime calls. Including this later documentation batch, the local completed estimate is **$16.512940** at this handoff checkpoint; it is not a remaining balance. Account-wide usage is uncertain, and credit freshness/shared caps are unchanged.

## Boundaries

AfterFix is a local configured-operator/viewer demo, not a multi-tenant or hosted product. Meta materially investigates through bounded read-only tools; Python/SQLite owns transactions and writes; the required independent Rust gate validates without database access or write authority. It supports evidenced duplicate inventory deductions only — not arbitrary SQL, production databases, unattended apply, or universal rollback. No final ZIP, video, live host, or public repository exists.

## Next acceptance items

- Responsive Chrome390x844/360x800/1280x800 layout correction is now checked in history/preview/dialog and approval/viewer states: [evidence](evaluations/mobile-layout-accepted-f6f91d55.json). Physical-device/live-mobile end-to-end behaviour was not measured.
- Final exact-value/no-leak review of source, history, reports, and screenshots — still pending; do not claim it is done.
- Separate OS/SQLite/toolchain and pip vendored-library advisory applicability review.
- Actual demo rehearsal, then recording.
- Before any public exposure: a hosting plan covering HTTPS, same-origin backend access, session isolation, credential provisioning/revocation, and shared limits.

Public repository creation/push, deployment, video publication, and contest submission are distinct owner-authorized tasks; none is approved or done here. Stop starting work by **2026-11-05 02:00 Bangladesh / 2026-11-04 20:00 UTC**, ahead of the **2026-11-05 08:00 Bangladesh** submission deadline.
