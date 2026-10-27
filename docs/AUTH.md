# Local demo access

AfterFix now requires bearer authentication for every API route except exact GET /health. This is a minimal single-demo-instance operator/viewer design, not individual accounts or a multi-tenant identity platform. Host only on loopback until release conditions in SECURITY_REVIEW.md/DELIVERY_PLAN.md are satisfied.

## Server configuration

Configure AFTERFIX_OPERATOR_TOKEN in the server process environment. AFTERFIX_VIEWER_TOKEN is optional. Use distinct randomly generated ASCII URL-safe tokens (letters, digits, hyphen, underscore),32..128 characters. There are no default credentials. Missing/invalid/colliding configuration fails closed with503. Never use the Meta API key as an application access token; the two have different purposes and destinations. Do not put tokens in source, URLs, reports or command arguments. Supply them through a protected local process environment/secret setup.

AFTERFIX_ALLOW_RESET=1 explicitly enables the synthetic reset route for the operator. It is disabled by default. This flag cannot permit a viewer to reset. Keep all demo data synthetic. Single shared instance credentials do not provide per-person attribution or private tenant/session datasets.

## Browser behavior

Enter only the AfterFix access key on the sign-in screen. The app checks GET /auth/me, then mounts the workspace with the server-returned role/capability. Application access keys stay in JavaScript memory and Authorization headers; no browser local/session storage, cookies, exports or URL parameters. Sign out and page reload clear the client session. This is client-side forgetting, not revocation of a static token; removing/changing the configured token revokes future requests. Already-started authorized work may finish after sign-out.

An operator may investigate/propose/approve/apply, and reset only if explicitly enabled. A viewer may read authorized demo records/patch/preview and call read-only verification; it cannot spend on investigation or mutate. This UI currently restores only incident/evidence on sign-in, not a previous session's proposal/report; report persistence is a later task.

## Backend controls and limits

- Authentication/permissions run before JSON validation, database work and Meta client dependency resolution. Only one Authorization header is accepted; comparisons are constant-time. Error bodies do not echo submitted credentials/fields.
- All protected HTTP request bodies are limited to64KiB of actual bytes with a5-second read deadline, including GET/chunked input. Oversized requests return413, timeout408.
- Investigation permits one active request per app process and three starts per60seconds. Excess requests return429/Retry-After without queueing/retrying paid calls. This does not replace the separate shared Meta credit/request guard. Multiple workers need coordinated outer limits before shared hosting.
- Approval stores the authenticated configured actor ID; apply checks that binding before mutation, cached retry success and already-applied shortcuts. Old approvals with blank actors cannot authorize a new apply. Internal trusted direct-engine calls default to local-core; the HTTP API never selects that identity from caller input.
- Reset/approval/apply audit rows record the actor. Records remain trusted synthetic SQLite data, not cryptographically tamper-proof evidence.
- The frontend constrains token-bearing requests to same-origin /api paths, refuses redirects and discards stale session responses. A stale401 cannot sign out a newer session. Server-side permission checks remain authoritative.

## Verified evidence

Full backend suite187 passed, one existing nonblocking Starlette/httpx deprecation warning. Strict frontend TS/Vite build passed. Lead's mocked-fetch security probe verified key shape, URL confinement, redirect refusal, stale/current401 ownership and sanitized header failures (no network). Authenticated real Chrome/Meta flow completed with final stock110 and10 passing preservation/verification checks. Viewer login/read-only controls, operator logout and reload-to-sign-in were observed. Direct actual-server probes returned401 for anonymous reads/reset and403 for viewer investigate/reset/approve/apply. Actual synthetic reports are in docs/evaluations/.

The owner's guarded preview launcher remains outside this public checkout; it creates/deletes ephemeral QA access tokens without printing them and injects the shared credit transport. A reproducible public bounded-runtime setup, hosted HTTPS/isolation and dependency advisory review remain release work. Authentication alone is not production certification.
