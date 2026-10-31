# Development

For the simplest supported installation, use the [local agent installer](INSTALL_AGENT.md). This page covers source development and the reviewed manual environment setup.

## Isolated Linux checks

From the repository root, Docker builds use the pinned toolchains in `deploy/Dockerfile` and make no live provider requests:

```sh
docker build --target qa -f deploy/Dockerfile -t afterfix-qa:review .
```

The QA target gates on the three native frontend state checks, strict TypeScript/Vite build, Rust stage and Python tests. See [deployment packaging](../deploy/README.md) for runtime/web targets and the manual GitHub workflow. This command builds/tests; it does not deploy.

## Frontend development checks

With the documented Node toolchain available:

```sh
cd frontend
npm ci --ignore-scripts
node scripts/check-api-session.ts
node scripts/check-case-state.ts
node scripts/check-scenario-state.ts
npm run build
```

These native checks are not browser/mobile or live-AI evidence. Use an isolated synthetic database for workflow/browser changes and record whether the model transport is real or injected offline.

## Manual Windows setup and recorded limits

The following commands and checkpoints were preserved from the earlier README. Read their dates and limits; they do not establish Windows Docker compatibility for the new agent. Current accepted counts are in the [project overview](../README.md#validation-you-can-inspect).

## Windows PowerShell setup

The lead validated pinned revision dd660c5 on Windows in separate fresh local clones: Python/Rust installation and tests, native checks and seven offline cases, plus fresh frontend install/build under npm12.0.2. This was split validation, not a single all-in-one run. Earlier failed attempts are retained; see [fresh frontend evidence](evaluations/frontend-repro-accepted-2026-10-28.json). Keep at least 1 GiB disk headroom before dependency installation; a full drive caused a recorded install failure.

From the repository root, create an isolated Python environment. Do not recreate an existing `.venv` automatically; create it only if it does not already exist:

```powershell
py -m venv .venv
```

Before installing the application requirements, use the explicit `.venv` Python (not an activation-dependent `python`) to upgrade the installer from the separate hash-pinned bootstrap file:

```powershell
.\.venv\Scripts\python.exe -I -m pip --isolated --disable-pip-version-check --no-input install --index-url https://pypi.org/simple --keyring-provider disabled --only-binary=:all: --no-deps --no-cache-dir --require-hashes --upgrade -r requirements-bootstrap.txt
.\.venv\Scripts\python.exe -I -m pip --version
```

The lead has now actually validated this exact hash-pinned bootstrap command on the project `.venv`: pip changed from 25.0.1 to 26.2.2, the other 23 PyPI distributions were unchanged, the explicit project pip reported 26.2.2, and `pip check` reported “No broken requirements found.” All six QA steps exited 0. See the [verified bootstrap evidence](evaluations/bootstrap-verified-v2/lead-review.json).

The version output must require pip 26.2.2. Stop on bootstrap failure or version mismatch rather than continuing or installing an unreviewed alternative. The bootstrap hash pins this installer wheel only; the application requirements are existing version pins, not a comprehensive hash lock.

Then install the application requirements as a separate step and check the environment:

```powershell
.\.venv\Scripts\python.exe -I -m pip --isolated --disable-pip-version-check --no-input install --index-url https://pypi.org/simple --keyring-provider disabled --only-binary=:all: --no-cache-dir -r requirements.txt
.\.venv\Scripts\python.exe -I -m pip check
```

The application-requirements install command above was **not rerun** as part of the bootstrap validation. The historical fresh-clone evidence predates the bootstrap change, so it does not by itself validate a complete fresh checkout using the new bootstrap.

Build and test the Rust verifier **before** running the Python suite, because the API integration requires the release binary:

```powershell
cargo build --locked --release --manifest-path rust-verifier/Cargo.toml
cargo test --locked --manifest-path rust-verifier/Cargo.toml
.\.venv\Scripts\python.exe -m pytest -q
```

The lead recorded Windows toolchain versions on 2026-10-28: **Python 3.12.10, Node v24.18.0, npm 12.0.2, rustc/cargo 1.98.0**. Cross-platform testing is pending.

## Run locally

Start the backend on loopback port 18157:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 18157
```

In a second PowerShell window, start the frontend:

```powershell
cd frontend
npm ci --ignore-scripts
npm run dev
```

The development frontend uses port **18173** by default and proxies API requests to the backend. For a production-style frontend build:

```powershell
npm run build
```

Do not use a default `uvicorn` port-8000 quick start with this demo; it conflicts with the configured local proxy expectation of port 18157.

Deterministic tests and offline routes do not require a Meta credential. A live investigation is explicitly paid and gated; it is not part of the deterministic test run.

## Access and live-runtime configuration

Before using protected routes, read [docs/AUTH.md](AUTH.md) and [docs/RUNTIME.md](RUNTIME.md).

- Configure the server-only `MODEL_API_KEY` through a protected process environment. Never put it in command arguments, source, browser storage, logs, or reports.
- Set `AFTERFIX_RUNTIME_DIR` to a trusted absolute path that already contains the existing shared runtime ledger for this project.
- All related processes must share that ledger. Do not create a second ledger for an existing project.
- Use the runtime initializer only for a genuinely new, zero-usage project. For an existing owner/shared ledger, never reset it and never start a new split ledger; follow [docs/RUNTIME.md](RUNTIME.md).
- Application access tokens are separate from the Meta key. Use distinct URL-safe tokens of 32–128 characters for operator and, optionally, viewer. There are no default or public QA credentials.
- For a public synthetic demo, explicitly set `AFTERFIX_PUBLIC_VIEWER=1`. The fixed demo's read-only GET routes open without a key; all anonymous mutations remain denied. The default remains private. The frontend always opens the dashboard, with an optional Operator access dialog.
- Reset is allowed only for the operator and only when `AFTERFIX_ALLOW_RESET=1` is explicitly set.
- The shared guard uses a $100 project cap, $50 reserve, and 24-hour credit-freshness check. Account-wide usage outside this ledger is uncertain, so no overage is assumed or authorized.
- Each investigation is bounded to 6 calls and an estimated $0.25 guard.

No private control path is required in a public checkout.
