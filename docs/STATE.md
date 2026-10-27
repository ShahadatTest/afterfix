# AfterFix state

## Status — 2026-10-27 Asia/Dhaka

Backend, recovery pipeline, bounded live Meta investigator and API integration are implemented and verified locally. Full Python suite: **148 passed, 1 nonblocking TestClient/httpx deprecation warning in 10.88s**. Product implementation and tests generated through Meta API; Codex planned, reviewed, ran tests and wrote controller infrastructure/documentation.

A REAL guarded Muse Spark1.3 investigation succeeded on an isolated synthetic DB: read actual handler patch and events1/3/4/5, created a deterministic +10 draft reversing event4. Three API calls, 3296 input/1054 output tokens, local estimate $0.0085995. Stock stayed100; approvals0, repair events0. See docs/evaluations/live-investigation-2026-10-27.json. Do not call fake test transports live AI. Separate explicit human API workflow preview/approve/apply/verify is tested.

The previous preservation false positive and incident-scope race are fixed with regressions. Historical event alteration/deletion, malformed baseline/references, wrong credit/linkage, stale data/incident metadata, forbidden tools, unknown citations, failed usage and concurrent repairs are tested. Trusted synthetic SQLite/event-identity assumptions apply; no cryptographic guarantee against an attacker rewriting every DB/approval record. No production service or hosting deployed.

## Current UI work — resume here

Sites skill was read for local UI design/preview guidance. Current work is local-only on the existing React/TypeScript + FastAPI checkout; skip Site registration/sync/publication. Keep Meta model as product implementation provider. Sites local preview reference was read; run local preview only when needed, verify successful HTTP then supported browser QA, and stop owned preview servers at teardown. No hosting migration to Workers.

Frontend scaffold is installed under frontend/. It is ONLY a placeholder App right now. Node24.18.0/npm12.0.2 are available; npm install --ignore-scripts --no-audit --no-fund completed (24 packages), lockfile generated. Dependency versions verified from public npm registry: React/DOM19.3.1, Vite8.3.5, plugin-react6.1.2, TypeScript7.0.2, React types19.3.0. Build/check not yet run on real UI.

CURRENT Meta UI generation: run91e8376b-12e9-43ad-8897-f872ea0046f2, command session83493; prompt ../afterfix-control/prompts/16-recovery-interface.md. Wonder Standard, medium reasoning, max24576 output; conservative request bound $0.64844. It returns only frontend/src/App.tsx/styles.css and optional types.ts/api.ts. Do not duplicate the request. Inspect saved run/budget ledger; review staged UI before installing it. Then run npm.cmd run build in frontend and browser-test actual workflow. Do not present placeholder as finished UI.

Next UI verification needs a local backend with the shared credit guard injected through get_investigation_client. Current default web route deliberately requires an explicitly configured loopback budget gateway and dummy credential; no unguarded official-API fallback. A lead-only local preview launcher may run BudgetedMetaTransport as a context, set app.dependency_overrides[get_investigation_client]=lambda:MetaClient(transport=guard), and serve Uvicorn on127.0.0.1:18157. The guard owns runner.lock for its lifetime, caps live calls/cost and validates current credit; stop preview before coding calls that need the same lock. Never use a fake model while labelling browser QA live. The real API key stays in control transport, not frontend/source/output. The preview launcher is not yet written.

Frontend dev port18173 and preview18174 bind127.0.0.1; /api proxies to backend18157. Avoid wildcard CORS. User must explicitly approve and then apply a specific preview; UI must reuse apply idempotency key across retries, stop on409, and render model/log/patch text safely.

## Accepted implementation / provenance

- Recovery/store: Wonder8ef0b921; preservation tests543ab76e + two arithmetic-only test fixesd9f10541.
- Runtime client/investigator: Wonder1cd897ba, then incident-scope recheck52b986de. Earlier87a7546b/c337408d drafts rejected, never installed.
- Offline client/investigator tests: b03abb3a; scope tests52b986de.
- Investigation API:9c1364a9, sanitized422 handler/import fix02835ba9. One invalid test expectation was corrected by c996f14e (a valid verify body correctly returns404; changed test body to invalid type). App was not weakened to satisfy bad tests.
- Frontend scaffold also c996f14e. Source-generation UI context suffixes now support .tsx/.ts/.css/.html; earlier failed controller preflight made NO API call.
- Full raw Meta responses, prompts and budget ledger stay outside this repo in ../afterfix-control/. Never send/read .env into tool output or model context. --draft-run supports validated staged source context without execution.

## Verification actually completed

- Full pytest:148 passed, 1 warning in10.88s after API validation/test corrections.
- Real Meta investigation:3 calls, grounded draft, stock/approval/repair unchanged as above.
- Independent preservation probe: valid later activity verifies; altered historical restock + deleted unrelated product is rejected.
- Independent scope-race probe exposed accidental live abstention row; Meta added under-lock complete incident recheck and six regressions; all now pass.
- Controller budget/path guard checks and per-model rate settlement checks passed; compile checks passed.
- Isolated Python dependency check passed previously; no new Python dependencies added.
- Exact key-value product-source scan passed before final backend/API additions; repeat secret scan before publication. No credentials in prompts or generated source by design.

## Credit and automation

Owner reported $150 available promotional credit on2026-10-27; credit-state.json is a24-hour owner_report gate, not a live provider balance. Completed-call local estimate before current UI generation is $2.9004135. Current request retains conservative reservation until API usage settles. Standard Spark and Wonder use per-model official rates; cached discounts are ignored conservatively. Shared local project cap$100 and $50 promotional reserve remain enforced. Other account activity is not visible; reconcile when reported/available. No overage/subscription/Contributor mode authorized.

Heartbeat afterfix-meta-model-lead ACTIVE every30minutes through2026-11-04 20:00UTC. Continue saved state with one model run at a time; quiet on unchanged/non-actionable state. Host/tools must be available; no claim of uninterrupted execution.

## Remaining delivery

1. Review/build complete UI and real browser flow; show actual preview when ready.
2. Broader held-out/live scenario evaluation and reproducible bounded runtime launcher/setup.
3. Deployment preparation, demo/video script and submission package.

No public repository/push, deployment, production change, legal acceptance, billing change or contest submission performed. Those require an authorized concrete task. Submission deadline2026-11-05 08:00 Bangladesh; stop initiating work2026-11-05 02:00 Bangladesh for buffer.
