# Release checklist

This checklist separates recorded evidence from work still pending. Checked items describe evidence already recorded in the linked project documents; they do not certify production readiness.

## Verified evidence

- [x] Backend suite: **403 passed**, one existing warning.
- [x] Rust: **17 unit + 15 CLI tests** and **184 independent cases** passed.
- [x] Strict TypeScript/Vite build passed: **22 modules in 228 ms**.
- [x] Native Node API-session and case-state checks passed.
- [x] Actual authenticated Spark 1.3 canonical recovery completed **100 → 110**, with one approval/repair and 10 preservation checks. Evidence: [browser recovery](evaluations/browser-rust-recovery-2026-10-28.json).
- [x] Durable version 1 reports/history, read-only reload, and exact JSON download implemented and checked in desktop offline browser QA.
- [x] Saved history restores automatically without restoring approval, checkbox, or apply state.
- [x] Browser reload forgets the memory token and requires sign-in.
- [x] Actual stale approval/apply `409` handling invalidated downstream state and refreshed to stock 101/5 events; `503` refresh failure retained old data with a warning. Evidence: [stale refresh](evaluations/browser-stale-refresh-2026-10-28.json).
- [x] Original frozen seven-case live benchmark retained at **5/7 passed, 2/7 failed**; H02/H04 failures were not rerun or relabelled. Summary: [evaluation results](EVALUATION_RESULTS.md).
- [x] The 2048 output-cap correction passed offline regressions; no live improvement is claimed.

## Pending validation — do not mark complete without new evidence

- [x] Fresh Windows frontend npm12.0.2 install/build passed after disk recovery; native dependencies installed,22modules222ms, emitted JS matches prior build SHA256. Earlier failures retained, no product fix needed. See [fresh frontend evidence](evaluations/frontend-repro-accepted-2026-10-28.json).
- [x] Pinned dd660c5 Windows reproducibility checked in separate fresh local clones: Python403/Rust32 tests and native checks/7offline apply cases, plus freshfrontend install/build. This was split validation, not a single all-in-one successful run; crossplatform remains pending.
- [x] Public OSV snapshot reviewed for103 Python/npm/Cargo triples:12IDs/six alias-grouped issues in pip25.0.1 installer; other102 no indexed matches. See [dependency results](DEPENDENCY_RESULTS.md).
- [x] Projectvenv hash-pinned pip26.2.2 bootstrap passed; onlypipchanged, compatibilitycheckpassed, new103-triple OSV snapshot completed0indexedmatches. Originalfindings retained; [version2 evidence](evaluations/bootstrap-verified-v2/lead-review.json).
- [x] Targeted installed runtime inventory/official advisory review recorded in [runtime review](RUNTIME_REVIEW.md); patch gaps found, no exhaustive OS/vendor clearance.
- [ ] Patched isolated Python/OpenSSL/SQLite/Node/Rust compatibility/rebuild checks and remaining OS/pipvendored review; historicalQAclones stillcontainoldpip. Passing current tests does not exclude Rust1.98.0 compiler issue.
- [x] Separate official hash-verified portableNode24.22.1: exactversion, TypeScript/Vitebuild andnativeAPI-session/case-state passed;18trackedfrontendfiles unchanged. [Evidence](evaluations/node-patch-accepted-3d199e2c.json). Python/OpenSSL/SQLite/Rust gates remainopen; global Node unchanged.
- [x] Meta-generated shared TLS trust correction in both default HTTPS transports: focused145/fullPython530passed, oneexistingwarning; [scoped evidence](evaluations/tls-accepted-b0866a8b.json). Fullruntime/toolchainpatching remains open.
- [x] Pinned0c4b1a6 committedsource/history exactcurrentMeta-key/encoded variants plus specificcredentialpatterns:129trackedfiles/303reachableblobs/4401177bytes,0matches. [Scoped evidence](evaluations/secret-scan-0c4b1a6.json).
- [ ] Remaining finalsecret/artifact review: lateredits, unknown/rotatedkeys, untracked/ignoredfiles, screenshots/OCR and eventualexportpackage; scopedscan is notall-artifactclearance.
- [x] Local pinnedf0144f6 source-review ZIP/manifest prepared:140files/380592bytes, allZIPmembers independentlymatchGitblobIDs/SHA256. Currentconfigured-key/encodedvariants+specificpatterns scan344reachableblobs/currenttree/ZIPmembers found0matches. [Scoped package evidence](evaluations/source-package-f0144f6-review.json); unknownrotatedkeys/OCR/private/lateredits remainoutside scope. No publication or runnablebundle claim.
- [x] ActualChrome responsive390x844/360x800/1280x800 CSSfixchecked: no pageoverflow inhistory/preview, dialogsfit, historycontrols>=44px, mobileapprovalseparation/viewerreadonly. [Post-fix evidence](evaluations/mobile-layout-accepted-f6f91d55.json); [originalfailure](evaluations/mobile-layout-finding-2026-10-28.json) retained. Browserviewportoverride, not physicaldevice/live-mobileE2E.
- [x] Actual delayed old-session report discarded after signout/viewer signin; viewer remained readonly. See [browser session evidence](evaluations/browser-session-failure-2026-10-28.json).
- [x] Actual UI/endpoint handling under simulated pre-provider budget denial and model-unavailable503; no new writes/retries or raw private sentinel. This does not claim live provider/ledger-budget rejection testing.
- [x] Actual patch/evidence Enter/Tab/Escape cycles and opener-focus restoration recorded in Chrome; native BODY/DIALOG intermediate focus observed, no underlying page action focused. Accessibility certification is not claimed.
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

## Current candidate delta —2026-10-30

- [x] Scenario Lab fullintegration, strictbuild and3native scripts accepted at7febe00.
- [x] Actual offline browser all3scenarios, viewer/session/failure paths,360/390/1280 layouts.
- [x] Remaining delayed-initialGET race and disabled404 browser checks passed.
- [x] Official portablePython3.14.10/privateRust1.98.1 compatibility:573Python/32Rust tests; existingglobal runtimes unchanged.
- [x] Real Meta release smoke: first provider failure retained; unsupported missingopening blocked; stale oldapply409; new snapshot101to111 verified with20+1restocks andGadget45 preserved. Frozen5/7 unchanged.
- [x] Actual live-run edited narrated demo rendered179.98seconds,1920x1080 H.264/AAC; sampled frames/duration checked, first failure retained.
- [ ] Current source/artifact scan/package and external publication.
- [ ] Linux deployment build/CI, isolated SKLab canary, DNS/HTTPS and rollback validation.
- [ ] Publicrepository/video publication and actual contestsubmission.

Historical pending items above are superseded only to the explicit scope of this delta. Remaining OS/SQLite/pip-vendored applicability and crossplatform deployment checks are not cleared by Windows tests.
