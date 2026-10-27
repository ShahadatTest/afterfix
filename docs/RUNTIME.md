# AfterFix portable runtime budget

`app/runtime_budget.py` provides `RuntimeBudgetTransport`, a guarded,
context-managed transport for the official Meta Responses API. It is used
only when trusted server-side code sets `AFTERFIX_RUNTIME_DIR` to an
existing runtime directory that already contains the shared ledger files:

- `budget-state.json` — shared coding/runtime ledger (version 1)
- `credit-state.json` — manually verified available-credit record
- `runner.lock` — pre-existing lock file (at least 1 byte)

The transport never creates, initializes or resets this ledger, never reads
credential files, and takes the API key only from the `MODEL_API_KEY`
process environment variable. It persists a conservative reservation before
every network call, reloads and revalidates both state files before each
call (including the 24-hour credit-freshness window and the $50 reserve),
and fails closed with a sanitized `runtime unavailable` error — no paths,
keys or upstream text — on any invalid state. Unresolved *request*
records (`reserved`, `unknown`, `needs_review`, ...) block further calls;
historical *run* labels do not, because the held runner lock already
excludes a collaborating process. Historical ledger records that predate
the model/rate fields are preserved verbatim and counted at their recorded
`charged_usd`; they are never repriced.

## Existing installations (the owner's directory)

If the three files above already exist, **do not run the initializer**.
Point `AFTERFIX_RUNTIME_DIR` at the existing directory and use it as-is.
Running the initializer against an existing installation would risk
creating a split/new ledger beside the real one, which is exactly what the
preflight below refuses to do.

Keep the runtime directory outside the source checkout and do not commit its
private usage notes or policy metadata. Only the trusted server process should
be able to change it; these local files are not tamper-proof billing records.

## Initializer (genuinely new, zero-usage projects only)

The empty history written here is valid **only** for a genuinely new
project with zero prior API usage. If any usage has already happened, an
empty history would understate spend: stop and record the real prior total
instead of initializing.

Before writing anything, preflight **all three** filenames and refuse if
**any** of them exists. Amounts are not hardcoded: supply a manually
checked, non-secret available amount and the recorded prior total through
explicit user-supplied variables. No credentials appear in this example or
its output.

```python
import json
import os

RUNTIME_DIR = "/absolute/path/to/new-runtime-dir"  # must already exist
AVAILABLE_USD = 0.0        # <-- user-supplied: manually checked available credit (non-secret)
PRIOR_TOTAL_USD = 0.0      # <-- user-supplied: recorded prior spend; 0.0 only if genuinely unused
VERIFIED_AT = 0            # <-- user-supplied: epoch seconds when AVAILABLE_USD was checked
SOURCE = "owner_report"    # or "provider_dashboard"

names = ["budget-state.json", "credit-state.json", "runner.lock"]
paths = [os.path.join(RUNTIME_DIR, n) for n in names]

if PRIOR_TOTAL_USD != 0:
    raise SystemExit("new zero-usage projects only; preserve and account for existing history")

# Preflight ALL three before creating ANY: refuse on any existing file.
if any(os.path.exists(p) for p in paths):
    raise SystemExit("refusing to initialize: a runtime file already exists; "
                     "use the existing directory and do not reset its ledger")

budget = {
    "version": 1,
    "basis": "new project, zero prior usage (confirmed by operator)",
    "total_cap_usd": 100.0,
    "requests": [],
    "runs": [],
}
credit = {
    "source": SOURCE,
    "available_usd": AVAILABLE_USD,
    "verified_at": VERIFIED_AT,
    "tracked_at_verification_usd": PRIOR_TOTAL_USD,
    "note": "manually checked by operator at setup",
}

# Exclusive creation for every file: no overwrite is possible even if a
# file appears between preflight and creation.
lock_fd = os.open(paths[2], os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o644)
try:
    os.write(lock_fd, b"\x00")
finally:
    os.close(lock_fd)
for path, value in ((paths[0], budget), (paths[1], credit)):
    with open(path, "x", encoding="utf-8") as handle:
        json.dump(value, handle)
print("initialized empty runtime ledger; no credentials recorded")
```

If creation fails partway, stop and inspect the newly created files. Do not
remove or reset an established runtime ledger. The initializer refuses
leftovers; recovery of a genuinely new, zero-usage directory needs explicit
review of exactly which files were created before trying setup again.

## Locking semantics

`RuntimeBudgetTransport` holds two locks for the whole context: an
in-process lock keyed by the resolved runtime directory, and the shared
`runner.lock` file lock.

- **Windows:** `msvcrt.locking(..., LK_NBLCK, 1)` locks exactly **byte 0**
  (length 1) of `runner.lock`; release unlocks that same byte range.
- **POSIX:** `fcntl.lockf(fd, LOCK_EX | LOCK_NB)` with the default length
  of 0 locks the **whole file** from the current offset (byte 0) to EOF;
  release uses `fcntl.lockf(fd, LOCK_UN)` over that same whole-file range.

Both are non-blocking: contention raises `RuntimeLockBusy`. A second
process in the offline tests locks the file the same platform-specific way
to prove cross-process exclusion.

## Operating rules

- One transport context per investigation; at most 6 calls and $0.25
  estimated spend per run, $100 shared project cap, $50 credit reserve.
- Credit state older than 24 hours, an invalid source or insufficient
  available amount, an unresolved request, or a usage record that breaches
  the conservative bound (`needs_review`) stops further calls. Refresh credit
  metadata only after actual verification; reconcile uncertain usage before
  resuming. Do not delete or rewrite history to clear such a stop.
- Deterministic offline tests live in `tests/test_runtime_budget.py` and
  use fake transports only; they make no real model requests.

Current fixed Spark Standard rates are $1.25/M input tokens and $4.25/M
output tokens, checked2026-10-27 against
https://dev.meta.ai/docs/pricing-rate-limits. Cached-input discounts are
ignored conservatively. Pricing/local estimates are not a provider invoice
or account-wide remaining balance.

## Run from this checkout

Install requirements.txt in an isolated Python environment. Configure the
trusted absolute AFTERFIX_RUNTIME_DIR and server-only MODEL_API_KEY through
a protected process environment; do not pass the key in command arguments.
Configure distinct application access keys as described in AUTH.md. The
browser receives only its application key, never the Meta key. Leave
AFTERFIX_META_BASE_URL unset in portable mode; configuring both modes fails
closed. The runtime imports no adjacent private control scripts.

Run `uvicorn app.main:app --host 127.0.0.1 --port 18157` in that environment.
For local development run `npm ci --ignore-scripts` and `npm run dev` inside
frontend/. The Vite proxy sends /api to the loopback backend. `npm run build`
checks TypeScript and produces static frontend assets; hosting/reverse-proxy
configuration for those assets is a separate release task. Do not expose a
Vite development server publicly.

Stop the API gracefully in its terminal so the current context finalizes
metadata and releases the lock. A killed process may leave a reservation;
do not retry a paid call until its outcome is reviewed. Existing loopback
gateway mode remains available only when explicitly configured instead of
portable mode; there is no unguarded direct fallback. Shared hosting still
requires HTTPS, appropriate data isolation and coordinated limits across
workers/hosts.
