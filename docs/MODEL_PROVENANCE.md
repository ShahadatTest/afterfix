# Implementation provenance

Product implementation and tests are generated through the official Meta Responses API using `muse-spark-1.3` in Standard mode. Codex authors task prompts, controller scripts, review notes and execution evidence; Codex stages/reviews Meta files and runs tests. No claim is made that the Meta model itself ran commands.

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
