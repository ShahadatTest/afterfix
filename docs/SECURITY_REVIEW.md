# Local security review — 2026-10-27

Codex inspected current source and ran bounded checks at the owner's request. This is a focused review, not a penetration test or security certification. No production, deployment, billing or live Meta requests were involved.

## Current stack verified

- Runtime: Python3.12.10; SQLite library3.49.1 from Python sqlite3.
- Backend requirements: FastAPI0.162.0, Uvicorn0.54.0, Pydantic2.14.1; pytest9.1.1 and httpx0.28.1 for testing.
- Frontend package/lock: TypeScript7.0.2, React/DOM19.3.1, Vite8.3.5, CSS/HTML. TypeScript compiles to JavaScript for the browser.
- Runtime model: fixed muse-spark-1.3 through official Meta Responses API; no browser credential or arbitrary model SQL tools.

## Existing controls

Queries bind user values using SQLite parameters; dynamic table names in reset are from a fixed internal list and event-ID queries generate placeholders. Investigation allows get_patch/read_events only. Model claims pass schema/citation/scope checks and deterministic repair validation. Preview uses a disposable database, approval binds proposal/snapshot, apply checks staleness under BEGIN IMMEDIATE and records idempotency/audit data. React renders records/model text as text; no dangerouslySetInnerHTML found. Local preview binds127.0.0.1. These controls reduce specific risks; none establishes user identity.

## Public deployment blocker

app/main.py has no user authentication/authorization middleware or route dependencies. Synthetic reset, proposal, approval, apply and evidence routes are accessible to any client that can reach the service. An approval ID represents repair consent/state binding, not an authenticated approver or role.

Independent TestClient probe used a temporary synthetic database, no authentication and no Meta calls. POST /admin/reset returned200; POST /proposals created a grounded draft; POST /proposals/{id}/approve returned200; POST /apply returned200. This confirms the gap without touching the saved demo DB.

Before exposing a shared demo, implement reviewed authentication/authorization or equivalent gateway isolation, scope access to the intended demo/session, remove or tightly restrict reset, bound incoming request rate/concurrent investigations, and serve behind HTTPS. No such implementation is claimed here. If cookie sessions are chosen, review CSRF/session protections as part of that design. Public deployment remains unauthorized and has not happened.

## Dependency checks actually run

- npm.cmd audit --json:0 reported known vulnerabilities across all reported severities at check time. This does not detect application logic vulnerabilities or guarantee future advisory coverage.
- .venv/Scripts/python.exe -m pip check:No broken requirements found. This checks compatibility, NOT Python vulnerability advisories; a backend/SQLite version advisory review is still needed before public deployment.
- Source inspection and isolated unauthenticated route probe as above; no exhaustive exploit testing or new product test suite authored.

## Database choice

SQLite remains suitable for this isolated synthetic demo; its file access permissions and application authorization matter. A shared multi-user production design should evaluate PostgreSQL for server roles, access policies and concurrent writes. Switching engines alone cannot fix unauthenticated application routes. PostgreSQL migration is not implemented or required merely to complete the current local demo.

Official references reviewed: https://sqlite.org/whentouse.html (one writer per database; client/server guidance), https://sqlite.org/security.html (untrusted SQL/database input precautions), https://sqlite.org/cves.html (applicability depends on input/attack preconditions), https://www.postgresql.org/docs/current/ddl-rowsecurity.html (policies/roles, owner/superuser caveats), https://www.postgresql.org/docs/current/ssl-tcp.html (encrypted client/server connections). No claim that a database is categorically the most secure.
