# Standalone Rust verifier — protocol 1

The `rust-verifier/` crate builds `afterfix-verifier` version0.1.0. It independently checks a supplied synthetic product ledger and an optional repair candidate. It reads stdin and writes stdout; it has no database/network/write/approval tools. This milestone is standalone: Python API approval/apply does not invoke it yet. A passing standalone response is not authorization to apply a repair.

## Build and test

From the repository root with Rust/Cargo installed:

```text
cargo test --locked --manifest-path rust-verifier/Cargo.toml
cargo build --locked --release --manifest-path rust-verifier/Cargo.toml
```

Cargo.lock pins resolved dependencies/checksums. Direct dependencies are serde1 with derive and serde_json1. Build artifacts under target/ are ignored. The evaluated host is Windows with rustc/cargo1.98.0. Cross-platform execution and dependency advisory review remain release checks; no speed or security certification is claimed.

## Request

Exactly one JSON object, with all fields required, including nullable fields:

| Object | Required fields |
| --- | --- |
| Request | protocol_version: integer1; product_id: string; recorded_stock: i64; events: array of Event; applied_proposals: array of Applied; proposal: Candidate or null |
| Event | id: i64; product_id: string; kind: string; qty: i64; order_id: string or null; reversed_ids: array of i64; repair_proposal_id: string or null |
| Applied | proposal_id: string; product_id: string; credit_qty: i64; reverse_event_ids: array of i64; status: string `applied` |
| Candidate | credit_qty: i64; reverse_event_ids: array of i64 |

Kinds are exactly `init`, `restock`, `order_deduction`, `repair_credit`. Kind/status accept strings only. Structs accept objects only. Unknown/duplicate keys, missing nullable fields, trailing documents and wrong numeric types fail decoding. Boolean, float and string values are not integers.

Input is bounded to524288 bytes before decoding; events, applied proposals and each reference list are bounded to200 entries. Every supplied string is1..128 UTF-8 bytes without ASCII control/DEL. Event IDs/references are positive signed64-bit integers; events must be in strictly increasing ID order, with gaps permitted. Timestamp/note fields are deliberately excluded from this normalized protocol; it does not independently validate chronology by timestamp or interpret natural-language evidence.

## Checks

All event quantities are positive, every record belongs to the target product, and opening history must contain at least one init. Multiple init records are allowed, matching existing Python semantics. Only deductions carry non-null order IDs; only repair credits carry nonempty reverse references or a repair proposal ID.

For each order, all deduction quantities must match. The minimum event ID is the original; later deductions are extras. Every arithmetic operation uses checked i64, including category subtotals. Actual ledger is `init + restock + repair credits - all deductions`, and must equal recorded stock. This conservative policy can reject a mathematically representable final result when an intermediate subtotal exceeds i64; it never wraps or silently truncates.

Each historical repair must reference a nonempty unique set of earlier extra deductions, never originals, absent IDs, future events or already reversed IDs. Its credit must equal the referenced sum. Each repair has exactly one matching applied proposal with matching product, amount, unique reference set and applied status. Duplicate/missing/unused applied records and reused proposal IDs fail.

Outstanding reverse IDs are all extras minus valid historical reversals. Credit is computed from their quantities, and expected stock is recorded stock plus outstanding credit. An independent cross-check is `init + restock - one deduction per order`. This second formula excludes historical repair credits because it already excludes all duplicate deductions. Valid later orders/restocks remain in both calculations.

With a candidate, positive credit and the exact complete outstanding reference set must match; a candidate for an already clean/repaired ledger fails. With proposal:null, a valid ledger may return computed outstanding duplicates for inspection, with proposal_matches:null. Inspection does not authorize a write.

## Response and failures

Exactly one JSON response plus newline, under16384 bytes:

```json
{"protocol_version":1,"verifier_version":"0.1.0","ok":true,"errors":[],"ledger_actual":100,"expected_stock":110,"outstanding_credit":10,"reverse_event_ids":[3],"proposal_matches":true}
```

This is an output-shape example, not a hardcoded result or evidence of a live model call. On failure numeric fields are null and reverse_event_ids is empty. Domain conflicts with a supplied candidate return proposal_matches:false; absent candidates and decoding/protocol failures return null. Error strings are fixed codes; input/parser/system details are not echoed. Input failures leave stderr empty.

Exit0 includes validly parsed domain failures (`ok:false`); consumers must check both exit status and response. Exit2 indicates argument, decoding or protocol failure. Codes include decode_error, invalid_args, protocol_error, limit_exceeded, invalid_id, event_order, invalid_string, missing_init, nonpositive_qty, product_mismatch, order_id_rule, repair_ref_rule, conflicting_qty, overflow, stock_mismatch, invalid_repair_ref, repair_credit_mismatch, repair_linkage, ledger_mismatch and candidate_mismatch. Unexpected CLI arguments are rejected without interpreting paths.

The map-shape preflight and typed decoding are complementary: preflight rejects array representations; original bytes are then decoded into strict typed structs so duplicate keys are still rejected. See [Serde's unknown-field behavior](https://serde.rs/container-attrs.html) and [field deserialization attributes](https://serde.rs/field-attrs.html).

## Remaining integration gate

The next task adds a fixed local executable adapter with shell disabled, bounded input/output, timeout and strict response validation. It must compare the bound current snapshot against Python results, and block approval/apply on unavailable verification, mismatch or failure. Apply must invoke checks inside its transaction; cached preview results cannot replace current-state checking. The accepted Python transaction/idempotency/preservation checks stay in force. No configurable arbitrary command, credential forwarding or silent success fallback is permitted.
