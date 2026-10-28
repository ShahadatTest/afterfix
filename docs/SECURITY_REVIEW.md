# Local security review — 2026-10-27

Codex inspected current source and ran bounded checks at the owner's request. This is a focused review, not a penetration test or security certification. No production, deployment, billing or live Meta requests were involved.

## Current stack verified

- Runtime: Python3.12.10; SQLite library3.49.1 from Python sqlite3.
- Backend requirements: FastAPI0.162.0, Uvicorn0.54.0, Pydantic2.14.1; pytest9.1.1 and httpx0.28.1 for testing.
- Frontend package/lock: TypeScript7.0.2, React/DOM19.3.1, Vite8.3.5, CSS/HTML. TypeScript compiles to JavaScript for the browser.
- Runtime model: fixed muse-spark-1.3 through official Meta Responses API; no browser credential or arbitrary model SQL tools.

## Existing controls

Queries bind user values using SQLite parameters; dynamic table names in reset are from a fixed internal list and event-ID queries generate placeholders. Investigation allows get_patch/read_events only. Model claims pass schema/citation/scope checks and deterministic repair validation. Preview uses a disposable database, approval binds proposal/snapshot, apply checks staleness under BEGIN IMMEDIATE and records idempotency/audit data. React renders records/model text as text; no dangerouslySetInnerHTML found. Local preview binds127.0.0.1. These controls reduce specific risks; none establishes user identity.

## Original access-control finding (before remediation)

The initial inspected app/main.py had no user authentication/authorization middleware or route dependencies. Synthetic reset, proposal, approval, apply and evidence routes were accessible to any client that could reach the service. An approval ID represented repair consent/state binding, not an authenticated approver or role.

Independent TestClient probe used a temporary synthetic database, no authentication and no Meta calls. POST /admin/reset returned200; POST /proposals created a grounded draft; POST /proposals/{id}/approve returned200; POST /apply returned200. This confirms the gap without touching the saved demo DB.

## Access-control remediation verified

Meta-generated auth middleware, operator/viewer permissions, restricted reset, incoming body/concurrency/rate bounds and actor-bound approval/apply/audit were reviewed and installed. Full suite187 passed; UI build passed. Actual server anonymous incident/reset probes now401; viewer investigate/reset/approve/apply now403. Authenticated real browser investigation/approval/apply/verification succeeded, with actor=operator in DB/audit/report. Viewer read-only UI, logout and reload-to-sign-in were observed. See AUTH.md and docs/evaluations/auth-route-probe-2026-10-27.json/browser-auth-recovery-2026-10-27.json.

This closes the original unauthenticated-route gap for the evaluated single-demo-instance design. It does not provide individual user accounts, multi-tenant/session dataset isolation, hosted HTTPS or distributed request limits. Those conditions and dependency advisory review remain before public exposure. The portable shared-credit runtime was subsequently implemented and verified through257 offline tests plus one actual default-runtime varied smoke; see RUNTIME.md and docs/evaluations/portable-varied-runtime-2026-10-27.json. Static application tokens require protected provisioning/revocation. The browser keeps its token only in memory; no cookie session was introduced. Public deployment remains unauthorized and has not happened.

## Dependency checks actually run

- Update2026-10-28: the completed public OSV snapshot covered103 pinned PyPI/npm/Cargo triples.12IDs all matched the fresh-clone pip25.0.1 installer, grouping into six GHSA/PYSEC alias pairs. The other102 had no indexed matches in this snapshot. Source hashes, mappings/counts and raw artifacts independently reviewed; see [dependency results](DEPENDENCY_RESULTS.md). Subsequent projectvenv hash-pinned pip26.2.2 upgrade passed with onlypipchanged, pipcheckpassed and new103-triple OSV query completed0indexedmatches; bothsnapshots preserved in dependency results. Retained historicalQAclones are not upgraded. This does not assess OS/SQLite/toolchain or pip vendored libraries and does not certify security.
- npm.cmd audit --json:0 reported known vulnerabilities across all reported severities at check time. This does not detect application logic vulnerabilities or guarantee future advisory coverage.
- .venv/Scripts/python.exe -m pip check:No broken requirements found. This checks compatibility, NOT Python vulnerability advisories; a backend/SQLite version advisory review is still needed before public deployment.
- Initial source inspection/isolated unauthenticated probe and later auth/actor regression suite plus actual-server/browser probes as above; no exhaustive exploit testing or certification.

## Database choice

SQLite remains suitable for this isolated synthetic demo; its file access permissions and application authorization matter. A shared multi-user production design should evaluate PostgreSQL for server roles, access policies and concurrent writes. Switching engines alone cannot fix unauthenticated application routes. PostgreSQL migration is not implemented or required merely to complete the current local demo.

Official references reviewed: https://sqlite.org/whentouse.html (one writer per database; client/server guidance), https://sqlite.org/security.html (untrusted SQL/database input precautions), https://sqlite.org/cves.html (applicability depends on input/attack preconditions), https://www.postgresql.org/docs/current/ddl-rowsecurity.html (policies/roles, owner/superuser caveats), https://www.postgresql.org/docs/current/ssl-tcp.html (encrypted client/server connections). No claim that a database is categorically the most secure.
