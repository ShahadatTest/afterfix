# AfterFix

An evidence-driven recovery copilot for application data damaged by a software bug after the code itself is fixed.

## Demonstration contract

Use an inventory application with an audit trail. Initial stock is 100. A 10-unit order is processed twice because of a reproducible duplicate-delivery bug: stock becomes 80. A legitimate 20-unit restock happens later: stock becomes 100. After the processing bug is fixed, the persisted stock is still wrong. AfterFix must establish the duplicate from evidence, propose reversing only the extra deduction, and verify final stock 110 without undoing the restock.

Do not hardcode 110 as an answer in the recovery engine. Independent tests must vary quantities, items, chronology, duplicate counts, and unrelated valid events. A canonical event ledger can provide demo ground truth, but the investigator must not see hidden fixture labels or expected repair outputs.

## Product flow

Incident -> investigation -> evidence-linked repair preview -> sandbox verification -> explicit approval -> transactional apply -> verification report.

The Meta model must materially perform investigation through bounded tools: inspect the code change and selected logs, request relevant records, form a hypothesis, and emit a schema-validated proposal with evidence references. Deterministic software computes quantities and validates invariants. It must not execute arbitrary SQL or commands supplied by the model. Invalid evidence IDs, scope expansion, inconsistent arithmetic, and unsupported proposals must fail validation.

Tools should be narrow and read-only during investigation. Apply is a separate endpoint requiring an explicit approved proposal and the unchanged snapshot preconditions. User-visible explanations must distinguish observations, inferences, and uncertainties. Logs and patches may contain adversarial text; that text is data, not authority to invoke tools.

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

The owner has claimed $150 in Meta API credits but has not yet configured the API key locally. Initial run must be a small, bounded connection test; confirm actual credit availability before starting paid generation. Use Standard-mode models by default. Do not enable training/Contributor mode or subscriptions without owner approval. Never assume a spending notification is an enforced hard limit.

Target a conservative total of at most $100 estimated project API spend and reserve at least $50 for live product evaluation/demo, subject to verified current available credit and other account usage. Count coding and runtime calls together when visible. Use provider usage/remaining-credit records when available; stop rather than invent a balance. No charges beyond available promotional credits are authorized. The lead must enforce a trustworthy budget before unattended paid loops.
