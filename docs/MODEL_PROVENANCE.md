# Implementation provenance

Product implementation and tests are generated through the official Meta Responses API using `muse-spark-1.3` and `muse-wonder-1.0` in Standard mode. Codex authors task prompts, controller scripts, review notes and execution evidence; Codex stages/reviews Meta files and runs tests. No claim is made that the Meta model itself ran commands.

Controller, credentials, raw requests/responses and cost ledger are outside this repository in the adjacent afterfix-control directory. Credentials are never prompt context. API response usage includes reasoning tokens within output tokens.

## Calls on 2026-10-27

| Run prefix | Purpose | Input tokens | Output tokens | Outcome |
| --- | --- | ---: | ---: | --- |
| connectivity-probe | Connection test | 16 | 19 | READY |
| 6c9f9b4c | Initial core | 2442 | 16384 | Output limit reached; only complete source files salvaged; incomplete tests discarded |
| e5615fa8 | Core review revision | 9535 | 20903 | Complete file object extracted; malformed trailing summary metadata discarded |
| e3d823a7 | Test-fixture fixes and real concurrency tests | 15328 | 7512 | One extra JSON envelope brace and doubly escaped line separators normalized; Python AST checked before review/execution |
| c06e5a4e | Preservation verification regression fix | 17373 | 24576 | Output limit; recovery has invalid indentation and tests truncated; rejected, never installed |

First three implementation calls plus connection test total approximately $0.22463 under the conservative local Standard-rate estimate. This is a local estimate from API usage, not a provider invoice or verified account-wide remaining balance. Including the rejected fourth implementation response, completed-call local estimate totals $0.350792. No generation is active; credential restoration is pending.

CLI preflight attempts made no model POST calls. Earlier rejected HTTP 400 connectivity probes produced no model response usage; they are not evidence of successful generation.

## Core acceptance revisions

| Run prefix | Model | Input | Output | Outcome |
| --- | --- | ---: | ---: | --- |
| 8cb54383 | Spark 1.3 | 10942 | 9474 | Rejected: syntax error and review-detected transaction/linkage defects; never installed |
| 8ef0b921 | Wonder 1.0 | 10918 | 12837 | Reviewed recovery/store preservation fix; original 18 tests passed |
| 543ab76e | Wonder 1.0 | 12783 | 7371 | Added preservation regressions; 44 passed, 2 incorrect test-oracle formulas failed |
| d9f10541 | Wonder 1.0 | 6543 | 3811 | Corrected only two test arithmetic formulas; complete suite 46 passed |

Local completed-call estimate at milestone-1 acceptance is $1.006090. Wonder Standard rates were verified on 2026-10-27 at https://dev.meta.ai/docs/pricing-rate-limits ($4/M input, $20/M output). Controller reserves worst-case text-token cost before each call, then reconciles reported usage. No subscriptions or Contributor mode enabled.

## Runtime and API acceptance

Wonder generated client/investigator1cd897ba, offline testsb03abb3a, scope guard/tests52b986de, API integration9c1364a9, validation handler02835ba9, and test correction/frontend scaffoldc996f14e. All source was reviewed before execution. Earlier runtime drafts87a7546b and c337408d were rejected, never installed. Final backend suite148 passed. The API validation test initially used a valid /verify payload and expected422; the Meta correction changed only that test request to an actually invalid type.

Actual live runtime evaluation used Spark1.3:3296 input/1054 output tokens over3requests, estimated$0.0085995; grounded draft+10, stock100 unchanged, approvals/repairs0. Public synthetic result is saved in docs/evaluations/live-investigation-2026-10-27.json; opaque model reasoning was not surfaced or replayed. Coding controller/client usage includes reasoning within output totals.

Completed local estimate before UI request91e8376b is$2.9004135; request bound$0.64844. Exact provider remaining balance is not known. No public hosting or contest submission occurred.

## Interface and browser validation

| Run prefix | Model | Input | Output | Outcome |
| --- | --- | ---: | ---: | --- |
| 91e8376b | Wonder1.0 | 7155 | 12610 | Full interface; reviewed before install; API shape problems found |
| cb6940ba | Wonder1.0 | 12381 | 11045 | Contract/mutation/stale/citation fixes; build found one nullable JSX guard |
| 3293587a | Wonder1.0 | 11506 | 9354 | Minimal null/status guard; strict TS/Vite build passed |
| live-bce0a2b7 | Spark1.3 | 3296 | 931 | Real desktop browser investigation,3 calls; separate approval/apply/verify and JSON export succeeded |

Browser synthetic recovery ended at stock110 with10 passing verification checks, one repair row, preserved restock and unrelated GADGET-9 stock45. Actual downloaded report is docs/evaluations/browser-live-recovery-2026-10-27.json. The model's observation/inference/uncertainty wording is preserved as model claims; gaps in global event IDs do not prove missing product evidence. Backend computes and validates the repair independently. No claim of held-out or mobile UI evaluation yet.

Local cumulative completed estimate now$3.69283825; no outstanding reservations. First preview teardown terminated the server without running its finalizer; all3 calls were already settled and ledger completion was reconciled under the shared lock after checking both owned ports closed. The lead-only launcher now supports graceful stdin stop. No credentials or raw opaque model reasoning are included in the public synthetic report.
