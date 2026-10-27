# Rust integration

AfterFix uses a fixed local Rust verifier as an agreement gate. Python owns SQLite, transactions, approvals, audit rows, and all writes. Rust receives a bounded normalized snapshot on stdin and returns bounded JSON on stdout; it has no database, network, approval, or write authority.

## Executable and protocol

Build the standalone crate before running integration tests:

```text
cargo build --locked --release --manifest-path rust-verifier/Cargo.toml
```

The adapter invokes only the executable derived from the repository root: `rust-verifier/target/release/afterfix-verifier` (`afterfix-verifier.exe` on Windows). It never accepts a caller-selected path or command. It uses `shell=False` and a minimal environment: empty on POSIX, and only validated `SYSTEMROOT`/`WINDIR` values on Windows. Secrets and unrelated environment variables are not passed.

Protocol version is `1` and verifier version is `0.1.0`. Requests contain at most 200 events and 512 KiB of JSON. Stored reference JSON is size-checked before parsing, and all quantities/IDs must be strict JSON integers in signed 64-bit range; booleans, floats, and numeric strings are rejected.

## Fail-closed behavior

Python independently validates history before launching Rust: recorded stock must equal the ledger, per-order quantities must be consistent, deduction/non-deduction order-ID and non-repair field rules must hold, historical repair references must be exact extra deductions linked to applied proposals, and all checked subtotals must fit in i64. Prior repair credits are excluded from outstanding duplicates by exact reversed IDs.

A present candidate is checked against the Python oracle before child launch. An invalid candidate fails immediately. An `ok:true` response may carry `proposal_matches:true` only when a candidate is present; it carries `null` for analysis-only requests, and `verified` is never returned for a false candidate. Failed responses may use `null` for `proposal_matches` as permitted by the Rust protocol, but they always raise in Python and are never verified. Response JSON is strictly decoded: exact keys, duplicate-key/trailing-data/NaN/invalid-UTF-8 rejection, strict integer types, finite allowlisted error codes, and agreement with Python-computed ledger values.

Preview reports Rust disagreement as a non-passing `rust_verification` and does not claim simulation success. Approval invokes the real fixed binary inside the current `BEGIN IMMEDIATE` transaction, bound to the current fingerprint and candidate, before approval/audit writes. Apply does the same inside its transaction before repair, stock, proposal, audit, or idempotency writes. Rust failure, disagreement, timeout, missing binary, malformed output, or output-limit failure blocks writes. A valid same-key replay is returned only after actor validation against the approval; a wrong actor is always rejected.

`verify_current(..., snapshot_hash=...)` does not trust the supplied label: it recomputes the fingerprint from the supplied connection and rejects a mismatch. Snapshot-bound callers pass a consistent copy/transaction.

## Limits and cleanup

The child has a 2.0-second execution deadline. Cleanup after timeout, overflow, read/write, start, or poll failure is bounded by an additional 1.0-second cleanup budget for kill/wait/join and stream closure; the public claim is therefore 2 seconds of execution plus bounded cleanup, not an unconditional 2-second total. Output and stderr are each capped at 16 KiB while reading; reading stops on overflow. Success requires process exit, complete stdin write plus EOF, no read/write/poll error, and threads that have finished. Streams are closed only after their owning I/O thread has finished, so the main thread does not block closing a stream against a still-running worker. Any cleanup failure is sanitized to a stable error code without child output, secrets, or filesystem paths.

Analysis-only verification (`proposal: null`) checks ledger/history agreement but is not an approved candidate and cannot authorize approval or apply.
