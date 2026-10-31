# AfterFix Linux deployment package

This directory contains deployment source. It does not initialize credit, seed a database, or prove that a hosted instance is live. DNS for the owner-selected `afterfix.sklab.cc` has been checked separately; HTTPS and runtime behavior require actual deployment checks. The initial draft was Meta generated; Codex implemented the Caddy correction following the owner's explicit direction on2026-10-31.

Actual2026-10-31 VPS validation is recorded in [deployment evidence](../docs/evaluations/hosted-deployment-2026-10-31.json): Linux573Python/32Rust tests, native frontend checks/build, hardened Caddy execution,15canary checks and HTTPS passed. The hosted app has private operator/viewer credentials and synthetic data. Live investigation is deliberately unavailable: no Meta key or hosted spending allocation has been provisioned while provider billing is blocked. A missing key returns503 before any provider request. The private accounting directory is reserved for later verified initialization; no fake credit state is supplied.

## Architecture

- `afterfix-api` runs the existing `app.main:app` with one Uvicorn worker on port 8000. It is not published on the host. The mandatory fixed Rust verifier remains at `/app/rust-verifier/target/release/afterfix-verifier`; missing, timed-out or disagreeing verification remains fail-closed. All existing authentication, actor, snapshot, idempotency, transaction, body, rate and credit guards remain in the application.
- `afterfix-web` uses the dedicated `web` Docker target derived from `caddy:2.10.2-alpine`. Build-time removal of the binary's file capability fixes the observed exec failure with all capabilities dropped; it does not change the shared upstream image or weaken container restrictions. It serves the extracted production frontend from a read-only bind at `/srv`, listens on 8080, strips only the `/api` prefix, and proxies to `afterfix-api:8000`. An API failure does not fall through to `index.html`. There is no Vite server in production.
- `afterfix_backend` joins only these services. It is intentionally not marked `internal: true`, because that could prevent the API's outbound HTTPS provider access. It has no published backend port.
- `afterfix_edge` is a pre-existing external Docker network for this application boundary. After backup and validation, the lead may attach the existing HTTPS edge proxy to it and add only an `afterfix-web:8080` upstream. Do not modify, stop, replace or prune unrelated services, routes or networks.
- The only host port here is the loopback canary `127.0.0.1:18881:8080`. Public domain and TLS routing are configured separately after canary and backup review. The internal Caddy container does not issue certificates.

Both containers use a read-only root filesystem, drop all capabilities, set `no-new-privileges`, run as uid/gid 10001, and have bounded memory, CPU and PID limits. Web `/config` and `/data` are small owner-writable tmpfs mounts so Caddy works without certificate persistence or a writable root filesystem.

## Build and manual CI

The GitHub workflow is `workflow_dispatch` only. With least-privilege `contents: read`, an Ubuntu 24.04 runner:

1. Builds the dedicated web image, executes its version command and validates `deploy/Caddyfile.web` with the actual read-only, nonroot, all-capabilities-dropped restrictions. It does not call a live API or model.
2. Builds the Dockerfile `qa` target. Its Python test run must pass before any artifact is created. Linux results are whatever CI actually reports; no historical Windows count is assumed.
3. Builds the `runtime` target as `afterfix:${GITHUB_SHA}`.
4. Creates—but never starts—a temporary container, copies `/opt/afterfix/frontend` into `bundle/frontend`, then removes only that temporary container.
5. Saves both runtime and web images as `bundle/afterfix-image.tar.gz`, adds the reviewed Compose and Caddy files, records `source_sha.txt` and both tags in `images.txt`, and generates `SHA256SUMS` covering every other bundle file.
6. Uploads one artifact named `afterfix-linux-${GITHUB_SHA}` for one day.

CI does not push an image, use deployment secrets, SSH anywhere, change DNS, accept terms, or deploy. A lead may run the same isolated builds on the authorized VPS after verifying disk and memory headroom. On2026-10-31 the VPS had75GiB free, so the previous nearly-full constraint no longer applies. Preserve all unrelated services and images; never prune them to make room.

Suggested local review commands (run by the lead, not implied to have been run here):

```sh
docker build --target qa -f deploy/Dockerfile -t afterfix-qa:review .
docker build --target runtime -f deploy/Dockerfile -t afterfix:review .
docker build --target web -f deploy/Dockerfile -t afterfix-web:review .
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 --tmpfs /config:uid=10001,gid=10001 --tmpfs /data:uid=10001,gid=10001 -v "$PWD/deploy/Caddyfile.web:/etc/caddy/Caddyfile:ro" --entrypoint caddy afterfix-web:review validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose -f deploy/compose.yml config
```

For the downloaded artifact, before loading anything:

```sh
cd bundle
sha256sum -c SHA256SUMS
cat source_sha.txt
docker load -i afterfix-image.tar.gz
```

Confirm the loaded image reference/tag matches the reviewed source SHA before use. Inspect `frontend/index.html` and the included deployment files; do not add environment files or private runtime data to the bundle.

## Host preparation

Create the external edge network only after reviewing existing networks, and create separate absolute host directories for data, private runtime accounting, and the extracted frontend. The data and runtime directories—and any files the application must create within them—must be writable by uid/gid 10001. The frontend directory and Caddyfile bind are read-only to the container but must be readable by uid 10001 on the host. Restrict host permissions appropriately, especially for `/runtime`; it is private accounting state, not application source or a credential store.

The lead initializes the shared runtime ledger/credit state separately with an explicit capped hosting allocation under the project cap/reserve policy. Do not invent, copy into this repository, or place budget/account files in the image or bundle. Credit freshness remains mandatory: when credit state is stale or otherwise invalid, model calls stop. Do not disable freshness, reserve, cap, concurrency or rate gates to make a canary pass.

A new synthetic SQLite database is created/loaded only through the operator's explicit existing reset/load action after startup. There is no startup seed or automatic investigation.

## Required private environment

The owner-approved public synthetic dashboard uses `AFTERFIX_PUBLIC_VIEWER=1` in the private runtime environment. This opt-in allows a narrow set of anonymous GETs and makes no model requests. The application's default is private. Anonymous POSTs, unknown routes and invalid credentials remain denied. Keep the operator key private; the dashboard's optional Operator access dialog accepts it in memory. Do not publish bearer tokens in browser assets or proxy configuration.

Supply credentials only through a private env file outside the repository and image, referenced by `AFTERFIX_ENV_FILE`. Compose reads it with `format: raw`, so characters in values are not interpolated by Compose. Never use build args, labels, inline Compose secrets, CI outputs, logs or commits for credentials.

The backend requires:

- `AFTERFIX_DB=/data/afterfix.sqlite`
- `AFTERFIX_RUNTIME_DIR=/runtime`
- `AFTERFIX_ALLOW_RESET=1`
- `AFTERFIX_ENABLE_SCENARIO_LAB=1`
- `MODEL_API_KEY`
- The existing distinct operator and viewer bearer-token variables expected by the application; each token must be 32–128 URL-safe characters.

No token values or examples are provided here. The four fixed `AFTERFIX_*` values are also set explicitly in Compose; the private file supplies credentials and must use the same fixed paths/flags where the application requires them. Rotate/revoke application credentials through the lead's private procedure, never by committing replacements.

## Compose variables

| Variable | Meaning | Required |
| --- | --- | --- |
| `AFTERFIX_IMAGE` | Verified runtime image tag | Yes |
| `AFTERFIX_WEB_IMAGE` | Verified dedicated web image tag; unmodified upstream is insufficient | Yes |
| `AFTERFIX_ENV_FILE` | Absolute path to the private host env file | Yes |
| `AFTERFIX_DATA_BIND` | Absolute host directory bound writable at `/data` | Yes |
| `AFTERFIX_RUNTIME_BIND` | Absolute private host directory bound writable at `/runtime` | Yes |
| `AFTERFIX_FRONTEND_BIND` | Absolute host directory containing the extracted frontend, bound read-only at `/srv` | Yes |
| `AFTERFIX_CADDYFILE_BIND` | Absolute host path to the reviewed `Caddyfile.web`, bound read-only | Yes |

All use Compose `${VAR:?}` requirements where applicable; missing values must stop configuration rather than fall back to a workspace-relative guess. Do not mount the Docker socket, SSH directories, a home directory or the control workspace.

## Canary checks

Start only these two services after reviewing `docker compose config`, the image digest/tag, bind ownership and private env-file permissions. Then check from the VPS loopback:

```sh
curl -fsS http://127.0.0.1:18881/healthz
curl -fsS http://127.0.0.1:18881/
```

Use an authenticated synthetic read-only request through `/api` to verify proxying and authorization behavior; a read-only viewer may inspect synthetic reports but must not mutate. Operator-only reset/scenario actions, approval and apply remain separate, private and explicitly confirmed. Do not perform a live model investigation merely as a container health check. The application `/health` endpoint is internal to the backend network/compose healthcheck and requires no model call.

Only after canary and backup review should the lead back up the existing edge configuration, validate it, add the separate `afterfix-web:8080` upstream for the chosen hostname, and preserve every unrelated route. No live HTTPS should be claimed until DNS, edge routing and certificate behavior have actually been verified.

## Rollback

Rollback is a lead-controlled operation: remove only the new edge upstream/route, restore the backed-up edge configuration and validate it; then stop/remove only the `afterfix-api` and `afterfix-web` services from this Compose project if necessary. Preserve `/data` and `/runtime` unless the lead explicitly decides their retention after backup review. Do not run blanket cleanup or prune commands, and do not delete unrelated images, containers, volumes or networks. Reverting the image means selecting the previously verified artifact/image tag and recreating only these two services; database or ledger restoration is a separate reviewed decision, not an unconditional rollback promise.

This package provides no tenancy isolation claim and no general production-SQL safety claim. It packages the bounded synthetic AfterFix release flow behind the existing single-instance operator/viewer controls.
