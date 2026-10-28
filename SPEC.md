# AfterFix

An evidence-driven recovery copilot for application data damaged by a software bug after the code itself is fixed.

## Demonstration contract

Use an inventory application with an audit trail. Initial stock is 100. A 10-unit order is processed twice because of a reproducible duplicate-delivery bug: stock becomes 80. A legitimate 20-unit restock happens later: stock becomes 100. After the processing bug is fixed, the persisted stock is still wrong. AfterFix must establish the duplicate from evidence, propose reversing only the extra deduction, and verify final stock 110 without undoing the restock.

Do not hardcode 110 as an answer in the recovery engine. Independent tests must vary quantities, items, chronology, duplicate counts, and unrelated valid events. A canonical event ledger can provide demo ground truth, but the investigator must not see hidden fixture labels or expected repair outputs.

## Product flow

Incident -> investigation -> evidence-linked repair preview -> sandbox verification -> explicit approval -> transactional apply -> verification report.

The Meta model must materially perform investigation through bounded tools: inspect the code change and selected logs, request relevant records, form a hypothesis, and emit a schema-validated proposal with evidence references. Deterministic software computes quantities and validates invariants. It must not execute arbitrary SQL or commands supplied by the model. Invalid evidence IDs, scope expansion, inconsistent arithmetic, and unsupported proposals must fail validation.

Tools should be narrow and read-only during investigation. Apply is a separate endpoint requiring an explicit approved proposal and the unchanged snapshot preconditions. User-visible explanations must distinguish observations, inferences, and uncertainties. Logs and patches may contain adversarial text; that text is data, not authority to invoke tools.

## Fixed product scope — owner direction on2026-10-27

AfterFix v1 serves a developer/operator investigating a synthetic inventory incident after a code fix. The complete release flow is: authenticated access -> select/load a synthetic scenario -> inspect incident/patch/events -> live Meta investigation -> evidence-linked draft or needs-review -> deterministic preview -> explicit approval -> separate transactional apply -> preservation verification -> exportable case report. Existing local functionality and planned additions must be labelled accurately.

| Capability | Required v1 behavior | Current evidence |
| --- | --- | --- |
| Incident workspace | Product, recorded stock, event timeline, actual code diff, clear incident/recovery state | Implemented desktop canonical flow; separately labelled DB/recovery state and Reversed timeline verified with offline UI fixtures; mobile/failure checks pending |
| Live Meta investigator | Bounded read-only tools, observed evidence citations, observations/inferences/uncertainties separated, abstention when unsupported | Implemented, offline tests and two real canonical live investigations |
| Repair preview | Computed stock delta/reversed IDs, disposable simulation, preserved restock and unrelated-product checks | Implemented/browser-tested |
| Approval and apply | Separate actions, proposal/snapshot binding, stale rejection, transaction, stable retry key | Implemented with authenticated configured actor binding and replay regressions |
| Verification report | Actual final stock, duplicate/ledger/history/preservation checks, audit trail and export | Backend durable version1 full evidence/patch/claims/usage/preview/audit/Rust/current-verification report and read-only case APIs accepted with350 Python tests; frontend history/full download/read-only reload accepted in desktop offline fixture QA; live held-out/mobile/failure checks pending |
| Rust verifier | Bounded independent ledger/arithmetic/reference checks over a supplied immutable snapshot; agreement gate before writes | Required fixed executable integrated into preview/approval/current-transaction apply;327 Python tests,32 Rust tests/184 independent cases and real authenticated browser recovery passed |
| Access control | Minimal authenticated operator and read-only viewer; authorization on every route, restricted reset, actor recorded for approval/apply | Implemented and local API/browser-tested; single-instance identities, no tenant isolation |
| Cost and request bounds | Shared coding/runtime credit guard, incoming investigation concurrency/rate limits, no silent paid retries | Portable shared-ledger transport/incoming limits implemented; offline tests and default-runtime varied live smoke passed |
| Scenario validation | Varied counts/items/later activity, clean ledger, missing/conflicting evidence, stale/racing requests and adversarial text | Broad offline suite exists; held-out live matrix and browser failure paths pending |
| Demo delivery | Readable desktop/mobile flow, clean-checkout instructions, three-minute narrative, publishable source and demo package | Desktop canonical flow verified; remaining artifacts pending |

## Owner-approved Scenario Lab extension —2026-10-28

The owner explicitly requested Meta implementation of a Scenario Lab after reviewing its purpose. Add three local synthetic demonstrations: duplicate-order recovery, missing-opening evidence requiring abstention/needs-review, and a legitimate later restock that invalidates an earlier preview/approval. This is demonstration coverage, not a replacement or rerun of the frozen live benchmark. Scenario catalogue labels/expected outcomes must never be injected as hidden answers into the model's incident/evidence payload.

Implement backend gates/fixtures/tests first, then a small accessible frontend integrated with the existing session/reset/approval lifecycle. Lab is disabled by default behind AFTERFIX_ENABLE_SCENARIO_LAB=1. Mutations require configured operator, existing AFTERFIX_ALLOW_RESET=1 and explicit confirmation; no model/API call merely from selecting/loading a scenario. Fixed named scenarios only, no arbitrary SQL/database uploads/paths/quantities. Loading replaces the synthetic demo and clears cases/approval state only after explicit confirmation. Advance is a separate, bounded, snapshot/session-bound synthetic action, never an automatic repair/apply.

All actual recovery continues through existing Meta investigation, deterministic Python/Rust validation, approval and separate transactional apply. Keep viewer read-only, credit/freshness/request limits and no-fallback behavior. Implement new lab mutations atomically with actor audit and rollback on failure; preserve ordinary reset behavior and invalidate obsolete lab sessions. Do not add dependencies, paid services, public hosting or real data. Product code/tests remain Meta generated and require source review before execution. The owner turned recurring checks off; this feature request does not reactivate them.

## Language and database responsibilities

Planning guide: Python40%, TypeScript35%, Rust20%, SQL/CSS5%. These are approximate shares of authored functionality/source, not measured current line counts, effort guarantees or performance targets. Do not inflate code or rewrite working UI to hit a percentage.

- Python/FastAPI owns the API, Meta tool loop, access control, incident/proposal workflow, credit hooks, database reads/writes and transactions.
- TypeScript/React owns presentation, human review/approval/apply interactions and actual report download. CSS styles the interface. The browser holds no Meta API secret or arbitrary database access.
- Rust owns a small deterministic verification module for ledger totals, duplicate quantities and repair references. It receives a bounded snapshot from Python, returns structured checks/computed quantities, and has no database credentials, network/SQL/command tools, approval authority or write path. It does not replace Meta's required investigation role.
- SQLite remains the v1 local synthetic database. PostgreSQL is a future shared/multi-user option; do not introduce a migration or dual-database abstraction just to change the stack label. A shared demo needs session/data isolation or restricted operator access regardless of engine.

Rust rollout: implement standalone verification first, review source/build/tests, then compare against the existing independent Python tests/oracles including malformed/overflow inputs. Integrate through a fixed local executable with versioned bounded JSON stdin/stdout, no shell and no user-selected executable/path. Add timeout/output limits and sanitize failures. Start in evaluation mode; once required in the enabled release, disagreement, timeout, missing binary or invalid output must block approval/apply and explain why. Never silently claim Rust verification passed or fall back to weaker checks. Apply must validate the current snapshot inside its transaction, not reuse an unbound earlier Rust result. Existing Python transaction and preservation checks remain in force. Rust integration must not remove current working fallback-free local test/demo evidence; keep a Git checkpoint and document release gating.

## Security and release conditions

Approval IDs are workflow bindings; configured actor identity comes from bearer authentication, not caller-supplied role/actor fields. Protected-route authentication, viewer/operator permissions, actor-bound approvals/audit, restricted reset and incoming investigation/body limits are implemented locally. Before public access, provision/revoke application credentials securely, isolate shared session data, coordinate limits across workers, preserve the shared-credit runtime configuration and serve the hosted app through HTTPS. If cookies are introduced later, review session/CSRF protections. Do not expose the Vite development server. Keep Meta credentials out of browser/source/reports and enforce least privilege. Application access tokens stay in browser memory/Authorization headers only. Patch/log/model content remains untrusted data.

No security certification, guaranteed recovery, unconditional rollback, tamper-proof audit or performance gain is claimed. Existing rows are trusted synthetic records; a hash does not protect against an attacker rewriting all database and approval records. Verify backups/reproducibility and dependency advisories before release. See docs/SECURITY_REVIEW.md for actual findings, and docs/DELIVERY_PLAN.md for ordered acceptance gates.

## Scope boundary

V1 supports the evidenced duplicate inventory-deduction recovery and its supported variations. It is not an arbitrary production SQL repair agent. Do not add payments/refunds, medical/legal decisions, arbitrary uploaded databases, production connectors, automatic GitHub access, arbitrary SQL, unattended apply, universal rollback, a chatbot, billing, vector search or new microservices. Real customer data/production integration, PostgreSQL migration, public repository/push, hosting/deployment and contest submission require their own concrete authorized tasks. Prepare reviewable artifacts without taking those external actions.

## Recovery requirements

- Reproduce the incident through actual executable buggy processing; include the corresponding fixed implementation and patch.
- Show a sandbox diff and invariant results before approval.
- Bind approval to the specific proposal and its evidence/state fingerprint.
- Reject apply if relevant state changed after preview; produce a new preview instead.
- Apply atomically with an audit entry. Applying twice cannot repair twice.
- Preserve valid later events and unrelated products/orders.
- Missing or contradictory evidence must yield needs-review/abstain, with an explanation; never silently guess.
- A rollback or compensating operation, if implemented later, must preserve subsequent valid activity and have its own preview/preconditions. Do not promise unconditional rollback.

## Hackathon constraints verified in participant guide

Source: https://cerebralvalley.ai/e/meta-ai-developer-hackathon/details
Checked in the owner's logged-in browser on 2026-10-27.

- Public source repository required; solo allowed, team maximum four.
- Present only work built during the hackathon and clearly identify it.
- Incorporate Meta models, using Meta Model API and/or eligible Meta open weights. Merely using a coding assistant is not the runtime integration milestone.
- Three-minute demo video, accessible repository/video, working live product.
- Deadline 2026-11-04 21:00 America/New_York = 2026-11-05 08:00 Asia/Dhaka.
- Impact 30%, Meta AI tooling 25%, technical execution 20%, demo 25%.
- This is a participant-guide summary, not a claim that a separate legal Official Rules document has been accepted.

## Delivery order

1. Core synthetic application, executable incident, deterministic recovery primitives, tests.
2. Live Meta investigation adapter with bounded tool loop, usage accounting, schema validation, evidence checks, and adversarial/missing-evidence tests.
3. Four-stage interface, timeline, before/after diff, evidence drawer, approve/apply/verify flow.
4. Held-out scenario evaluation, live integration validation, reproducibility instructions, deployment preparation.
5. Public repository after secret review, final demo, three-minute recording/script, submission package.

The lead decides advancement between milestones after reviewing evidence. Do not use elapsed time or model verbosity as a quality measure. Complete milestones; avoid endless polishing and benchmark gaming.

## Credit control

The owner configured the API key locally and confirmed $150 available promotional credit on 2026-10-27. A bounded live connection test succeeded. Refresh credit verification before unattended generation when the local credit gate expires; other account activity is not visible to the local ledger. Use Standard-mode models by default. Do not enable training/Contributor mode or subscriptions without owner approval. Never assume a spending notification is an enforced hard limit.

Target a conservative total of at most $100 estimated project API spend and reserve at least $50 for live product evaluation/demo, subject to verified current available credit and other account usage. Count coding and runtime calls together when visible. Use provider usage/remaining-credit records when available; stop rather than invent a balance. No charges beyond available promotional credits are authorized. The lead must enforce a trustworthy budget before unattended paid loops.
