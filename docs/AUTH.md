# Authentication and access

AfterFix uses minimal bearer-token access for one local synthetic demo instance. It is not an identity platform, does not provide per-person or multi-tenant accounts, and is not a security certification.

Every API route except the exact public `GET /health` requires authentication.

## Roles

- **Operator:** may read demo data, run investigations, preview, approve, apply, and use reset only when reset is explicitly enabled.
- **Viewer:** may read authorized demo data, previews, saved cases, and reports, and use read-only verification. A viewer cannot investigate, approve, apply, or reset.

Approval and apply are separate actions. Authentication never creates automatic approval.

## Server configuration

Configure these values only through a protected server process environment or an approved secret mechanism:

- `AFTERFIX_OPERATOR_TOKEN` — required operator application token.
- `AFTERFIX_VIEWER_TOKEN` — optional, distinct viewer application token.
- `AFTERFIX_ALLOW_RESET` — set to exactly `1` only when synthetic reset is intentionally enabled for the operator.

Application tokens must be distinct, randomly generated, ASCII URL-safe values of **32 to 128 characters**. There are no default credentials and no public QA credentials. Missing, invalid, or colliding configuration fails closed.

Never use the Meta API key as an application token. The application token, Meta key, and runtime-ledger controls serve different purposes. Never place credentials in source, URLs, command arguments, browser storage, logs, screenshots, or reports.

Reset affects only local synthetic demo data. The flag does not give a viewer reset permission.

## Browser behaviour

The browser keeps the application token only in memory and sends it in `Authorization` headers. It does not store the token in local storage, session storage, cookies, exports, or URL parameters.

- Sign-in checks `GET /auth/me` and uses the server-returned role and capabilities.
- Sign-out clears the client session.
- A browser reload forgets the in-memory token and requires sign-in again.
- Already-started authorized work may finish after sign-out.
- This is client-side forgetting, not revocation. Changing or removing the configured server token is required to revoke future requests.
- Saved case history restores automatically after sign-in.
- Restored history is read-only. It never hydrates approval, checkbox, or apply state.
- Any new mutation requires a fresh, explicit investigation, preview, review, approval, and separate apply.

Server-side authorization remains authoritative even if browser controls are hidden or disabled.

## Backend controls

- Authentication and permission checks run before protected work.
- Only one `Authorization` header is accepted, and token comparison is constant-time.
- Error responses do not echo submitted credentials.
- Protected request bodies are limited to 64 KiB with a bounded read time.
- Investigation concurrency and start rate are limited per application process; excess requests fail without queueing a paid retry.
- Approval records the authenticated configured actor. Apply checks that actor binding before mutation or an already-applied shortcut.
- Reset, approval, and apply audit records include the actor.
- Records are trusted synthetic SQLite data, not tamper-proof evidence.

Multiple workers or hosts need coordinated limits and data isolation before any shared hosting. Authentication alone does not make the local demo suitable for public production use.

## Verified evidence

- The latest full backend run recorded **403 passed** with one existing warning.
- Native API-session and case-state checks passed.
- An actual authenticated Chrome recovery using real Spark 1.3 completed stock **100 → 110** with one actor-bound approval/repair and 10 preservation checks.
- Anonymously protected requests and viewer mutation attempts were denied in local testing.
- Actual stale approval/apply handling invalidated downstream state; successful refresh showed stock 101 and 5 events, while a failed refresh retained old data with a clear warning.

Evidence: [canonical browser recovery](evaluations/browser-rust-recovery-2026-10-28.json) and [stale-refresh checks](evaluations/browser-stale-refresh-2026-10-28.json).

Hosted HTTPS, session/data isolation, coordinated limits, access-key provisioning and revocation procedures, dependency review, and secret review remain release tasks. See [RUNTIME.md](RUNTIME.md) for the separate Meta runtime guard and [STATE.md](STATE.md) for current limitations.
