# AfterFix

Evidence-driven recovery for an inventory double-delivery bug. The local React interface completes investigation, preview, explicit approval, repair and verification through FastAPI. A real Spark1.3 browser investigation identified a duplicate deduction; Rust independently checked the ledger before approval/apply, and the repair restored stock from100 to110 while preserving the later restock and unrelated product. Actual exported report: docs/evaluations/browser-rust-recovery-2026-10-28.json. Backend suite327 passed; frontend strict TypeScript/Vite build passed previously. See docs/STATE.md for evidence and remaining validation.

Fixed v1 scope and ordered acceptance gates are in SPEC.md and docs/DELIVERY_PLAN.md. Operator/viewer access control, actor-bound approval/audit, required Rust agreement gates and portable bounded runtime are implemented and verified locally. Complete case reports and held-out evaluations remain. Current database is SQLite; a PostgreSQL migration is outside local v1. Public hosting/security release work remains incomplete; see docs/SECURITY_REVIEW.md and docs/AUTH.md.

## Quick start (lead to run, isolated env)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cargo build --locked --release --manifest-path rust-verifier/Cargo.toml
pytest -q
uvicorn app.main:app --reload
```

Frontend: run `npm ci --ignore-scripts` then `npm run dev` in frontend/. Its `/api` proxy expects the backend on127.0.0.1:18157, so use `uvicorn app.main:app --host 127.0.0.1 --port 18157` for that interface. `npm run build` performs strict TypeScript validation and builds assets.

Live investigation additionally needs the bounded Meta transport and fresh shared budget/credit metadata described in docs/RUNTIME.md. Set the trusted absolute AFTERFIX_RUNTIME_DIR and server-only MODEL_API_KEY through a protected process environment; all coding/runtime processes for the same project must share the same ledger. Do not initialize a second ledger or pass secrets in CLI arguments. The portable code imports no adjacent private controller; existing explicitly configured loopback gateway mode remains supported instead. There is no unguarded direct-provider fallback. Deterministic routes/tests work without Meta credentials. A real default-runtime varied smoke produced a295-to318 preview without approving/applying or changing live stock; see docs/evaluations/portable-varied-runtime-2026-10-27.json.

Before using protected routes, configure distinct server application access tokens as described in docs/AUTH.md. Then sign in through the local interface with the appropriate AfterFix key. These are not Meta API credentials. Reset requires an operator plus AFTERFIX_ALLOW_RESET=1 and affects only synthetic demo data. No token defaults, URL/body credentials or unauthenticated seed route exist. GET /health remains public.

Flow: `POST /proposals` (draft) -> `GET /proposals/{id}/preview` (sandbox simulation on a disposable backup) -> `POST /proposals/{id}/approve` (human route, binds proposal+fingerprint) -> `POST /apply` (human route, transactional + idempotent) -> `POST /verify`.

`reset` is local synthetic only and is intentionally absent from `TOOL_MANIFEST`. The manifest exposes investigation tools only; model output must never contain approve/apply/reset calls.

## Integrity notes

Rust verifier: `cargo test --locked --manifest-path rust-verifier/Cargo.toml`. Its bounded JSON protocol and checked arithmetic/reference checks are in docs/RUST_PROTOCOL.md; required fixed-executable API integration is in docs/RUST_INTEGRATION.md. Missing binary, disagreement or invalid output blocks approval/apply without a fallback. Build the release artifact before running the recovery API/tests.

- Apply holds `BEGIN IMMEDIATE` before any reads; same-key retries are validated against the bound proposal/approval; different keys cannot double-repair.
- Evidence must cite both the original and every duplicate deduction; prior repairs are recorded as explicit reversed IDs.
- Preview simulates on a disposable backup; live DB stays unchanged; approval/apply revalidate staleness.
- No unconditional rollback is claimed; verify checks ledger match, zero outstanding duplicates, preserved restocks (not required to exist), and unchanged unrelated products.
