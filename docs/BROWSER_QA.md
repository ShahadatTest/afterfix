# Browser QA plan and evidence boundaries

Internal QA only. Synthetic data and synthetic public test tokens only. No real Meta key in a browser, no arbitrary public debug endpoint, no deploy/publish/account changes.

## Evidence categories (never conflate)

| Category | What it proves | Status |
| --- | --- | --- |
| (a) Native Node mocked API regression — `frontend/scripts/check-api-session.ts` | Public `api.ts` behaviour against a fake `fetch`: header/redirect handling, stale-generation discard (deferred success, deferred 401, pending body read, fetch rejection), current-401 token clear + callback ownership, sanitized network/read/JSON and 403/409/429/503 errors, no retry, path containment before credential attachment, access-key shape bounds, malformed-token header failure. Uses only fixed synthetic A/B tokens; default fetch is a throwing sentinel. | Script provided; lead runs it. NOT browser or mobile evidence. |
| (b) Native Node pure case-state regression — `frontend/scripts/check-case-state.ts` | Pure `caseState.ts` label/verification/Rust-readiness/identity logic. No network, React or DOM. | Existing script; lead runs it. NOT browser evidence. |
| (c) Actual authenticated desktop OFFLINE browser evidence already recorded | Earlier Chrome desktop run under an offline FakeMeta fixture with real FastAPI/SQLite/Rust: canonical recovery to stock 110, history restore, exact-byte report download, read-only reload, viewer-disabled mutations, needs-review history, reset, Reversed timeline. Fixture output is not live-model evidence. | Recorded (see `docs/STATE.md` / offline UI summaries). Desktop only; do not relabel as mobile or live. |
| (d) Remaining actual browser/mobile checks | Real React/browser behaviour under delay, failure, stale state, keyboard and small viewports. | **PENDING** — see checklist below. |

Known limitation to verify on first run of (a): case 6 assumes the Node 24 `Headers` implementation rejects a value containing a newline, matching browser behaviour. If Node accepts it, that assertion will fail against current `api.ts`; report the actual failure to the lead rather than weakening the test or changing product code without a focused correction task. The 409 assertion in (a) checks only the API message wording; it is not React stale-state invalidation evidence.

## Runnable commands (repo root, lead executes)

```sh
node --experimental-strip-types frontend/scripts/check-api-session.ts
node --experimental-strip-types frontend/scripts/check-case-state.ts
npm --prefix frontend run build
```

Expected: each script prints its pass line and exits 0; the build is strict `tsc` + Vite. A mocked/native pass must be reported exactly as "mocked fetch regression" or "pure case-state regression", never as browser/mobile pass.

## Remaining actual browser checks — PENDING

Run only under the reviewed loopback OFFLINE fixture, controlled by the lead, with synthetic public test tokens. No server/browser work is part of the mocked-script task.

1. **Delayed report fetch, then sign-out / new sign-in** — delay a saved-report fetch, sign out and sign in as the other synthetic identity before it resolves; the late response must not render the previous session's report, resurrect controls, or overwrite the new session's state.
2. **Unavailable backend / model budget failure** — stop or fail the backend (and separately simulate model-unavailable/budget failure); the UI must show a sanitized failure, preserve existing evidence, and must not automatically re-run the investigation or spend again on retry/reload.
3. **Real stale 409** — after a preview, make a controlled synthetic event change, then attempt approval/apply with the stale preview; expect 409, and confirm the preview, approval checkbox and approval action are invalidated and a fresh investigation is required before any write.
4. **Viewer restrictions** — signed in as the synthetic viewer, all write controls (investigate/approve/apply/reset) are disabled or absent and direct interaction cannot trigger a write.
5. **Keyboard dialogs** — patch/evidence dialogs open, trap focus sensibly, close with Escape, and return focus using keyboard only; approve/apply remain separate deliberate actions.
6. **Layouts** — 390x844 and 360x800 (mobile) plus 1280x800 (desktop): no clipped primary buttons, timeline/history readable, page and panel scrolling sane, report download reachable.
7. **No unauthorized or double writes** — after the stale, delayed and retry cases above, independently inspect the synthetic DB: exactly the expected repair count, restock and unrelated records preserved, no duplicate apply from a repeated/stable retry key.

## Reporting rule

Each item in (d) stays **PENDING** until an actual browser run records viewport, fixture, steps and result. Mocked or pure Node passes, offline desktop evidence, and live-model results are separate ledgers and must not be merged into a single "passed" claim.
