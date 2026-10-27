# AfterFix

Evidence-driven recovery for an inventory double-delivery bug. The local React interface completes investigation, preview, explicit approval, repair and verification through FastAPI. A real Spark1.3 browser investigation identified a duplicate deduction; the approved repair restored stock from100 to110 while preserving the later restock and unrelated product. The exported synthetic report is in docs/evaluations/browser-live-recovery-2026-10-27.json. Backend suite148 passed; frontend strict TypeScript/Vite build passed. See docs/STATE.md for evidence and remaining validation.

## Quick start (lead to run, isolated env)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q
uvicorn app.main:app --reload
```

Frontend: run `npm ci --ignore-scripts` then `npm run dev` in frontend/. Its `/api` proxy expects the backend on127.0.0.1:18157, so use `uvicorn app.main:app --host 127.0.0.1 --port 18157` for that interface. `npm run build` performs strict TypeScript validation and builds assets.

Live investigation additionally needs a bounded Meta transport. This checkout intentionally has no direct-provider fallback: the default dependency accepts an explicitly configured loopback budget gateway. The owner's reviewed preview launcher and shared promotional-credit accounting live outside this repository. A standalone public setup is still pending; do not present the quick start alone as sufficient for live AI. Deterministic routes and tests work without Meta credentials.

Seed and inspect (local synthetic only):

```bash
curl -X POST http://127.0.0.1:8000/admin/reset
curl http://127.0.0.1:8000/tools/manifest
curl http://127.0.0.1:8000/incidents/INC-001/evidence
```

Flow: `POST /proposals` (draft) -> `GET /proposals/{id}/preview` (sandbox simulation on a disposable backup) -> `POST /proposals/{id}/approve` (human route, binds proposal+fingerprint) -> `POST /apply` (human route, transactional + idempotent) -> `POST /verify`.

`reset` is local synthetic only and is intentionally absent from `TOOL_MANIFEST`. The manifest exposes investigation tools only; model output must never contain approve/apply/reset calls.

## Integrity notes

- Apply holds `BEGIN IMMEDIATE` before any reads; same-key retries are validated against the bound proposal/approval; different keys cannot double-repair.
- Evidence must cite both the original and every duplicate deduction; prior repairs are recorded as explicit reversed IDs.
- Preview simulates on a disposable backup; live DB stays unchanged; approval/apply revalidate staleness.
- No unconditional rollback is claimed; verify checks ledger match, zero outstanding duplicates, preserved restocks (not required to exist), and unchanged unrelated products.
