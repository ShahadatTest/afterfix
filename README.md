# AfterFix (Milestone 1)

Evidence-driven recovery for an inventory double-delivery bug. Meta-generated backend and live investigation have 148 passing tests. A real Spark1.3 investigation read evidence and created a draft without changing stock or approving/applying it. The interface is being implemented; the current frontend is a scaffold. See docs/STATE.md for actual evidence and remaining work.

## Quick start (lead to run, isolated env)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q
uvicorn app.main:app --reload
```

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
