# Release checklist

This checklist separates recorded evidence from work still pending. Checked items describe evidence already recorded in the linked project documents; they do not certify production readiness.

## Verified evidence

- [x] Backend suite: **403 passed**, one existing warning.
- [x] Rust: **17 unit + 15 CLI tests** and **184 independent cases** passed.
- [x] Strict TypeScript/Vite build passed: **22 modules in 194 ms**.
- [x] Native Node API-session and case-state checks passed.
- [x] Actual authenticated Spark 1.3 canonical recovery completed **100 → 110**, with one approval/repair and 10 preservation checks. Evidence: [browser recovery](evaluations/browser-rust-recovery-2026-10-28.json).
- [x] Durable version 1 reports/history, read-only reload, and exact JSON download implemented and checked in desktop offline browser QA.
- [x] Saved history restores automatically without restoring approval, checkbox, or apply state.
- [x] Browser reload forgets the memory token and requires sign-in.
- [x] Actual stale approval/apply `409` handling invalidated downstream state and refreshed to stock 101/5 events; `503` refresh failure retained old data with a warning. Evidence: [stale refresh](evaluations/browser-stale-refresh-2026-10-28.json).
- [x] Original frozen seven-case live benchmark retained at **5/7 passed, 2/7 failed**; H02/H04 failures were not rerun or relabelled. Summary: [evaluation results](EVALUATION_RESULTS.md).
- [x] The 2048 output-cap correction passed offline regressions; no live improvement is claimed.

## Pending validation — do not mark complete without new evidence

- [ ] Clean-checkout setup and reproducibility using the documented Windows PowerShell commands.
- [ ] Python, JavaScript, and Rust dependency advisory review.
- [ ] Final secret review of source, reports, screenshots, and exported artifacts.
- [ ] Actual mobile layout checks; the recorded browser viewport was desktop **1280 × 585**.
- [ ] Delayed React/session/report failure checks.
- [ ] Model-budget browser failure checks.
- [ ] Complete keyboard cycle, including native dialog focus behaviour.
- [ ] Cross-platform testing; current Windows versions recorded2026-10-28 are Python3.12.10, Node24.18.0, npm12.0.2, rustc/cargo1.98.0.
- [ ] Demo rehearsal and then recording; neither is claimed yet.
- [ ] Any live retest of the output-cap correction under a separately approved budget and plan.

## Hosted-demo review

The static frontend build does not include the Vite development proxy. Before any hosted demo can work, separately review and implement the backend route behind the static host, HTTPS, session/data isolation, coordinated request and credit limits, and access-key provisioning/revocation.

`npm run preview` serves the frontend only and does not provide a production API proxy; it must not be described as a working hosted product. Do not expose the Vite development server publicly. No deployment implementation or new framework is authorized by this checklist.

## Separate owner tasks

The following are preparation-only here and each needs an explicit owner task:

- [ ] Create and push the public source repository.
- [ ] Provision hosting and publish the live demo.
- [ ] Record and publish the three-minute video.
- [ ] Review contest terms, billing implications, and submit the entry.

## Submission timing guardrail

Contest deadline: **2026-11-04 21:00 America/New_York**, which is **2026-11-05 08:00 in Bangladesh**. Stop starting new work by **2026-11-04 20:00 UTC** (**2026-11-05 02:00 Bangladesh**) to preserve a submission buffer.

## Claim limits

Do not claim security certification, production readiness, perfect scores, universal rollback, arbitrary SQL repair, mobile verification, or measured live improvement from the offline output-cap fix. Keep offline fixtures separate from live evidence.
