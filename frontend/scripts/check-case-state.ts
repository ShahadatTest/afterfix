// Pure regression checks for frontend/src/caseState.ts. No JSX/React tests.
// Run: node --experimental-strip-types frontend/scripts/check-case-state.ts
import assert from "node:assert/strict";
import {
  caseRecoveryLabel,
  hasMatchingCaseIdentity,
  isActiveVerified,
  isCaseVerified,
  isRustApprovalReady,
  isValidPassingChecks,
} from "../src/caseState.ts";

const validReport = {
  schema_version: 1,
  case_id: "CASE-1",
  incident_id: "INC-001",
  record_status: "ok",
  recovery_state: "verified",
  verification_status: "passed",
  final_verification: {
    pass: true,
    preservation_verified: true,
    checks: [{ name: "one", ok: true }],
  },
};
const clone = (v: unknown): any => JSON.parse(JSON.stringify(v));
const label = (v: unknown) => caseRecoveryLabel(v).label;

// Valid baseline.
assert.equal(isCaseVerified(validReport), true);
assert.equal(label(validReport), "Verified");

// Each single false/missing/malformed/contradictory change must NOT verify.
const badReports: Array<[string, unknown]> = [
  ["malformed extra check entry", { ...clone(validReport), final_verification: { ...clone(validReport.final_verification), checks: [{ name: "one", ok: true }, {}] } }],
  ["null check entry", { ...clone(validReport), final_verification: { ...clone(validReport.final_verification), checks: [null] } }],
  ["false check", { ...clone(validReport), final_verification: { ...clone(validReport.final_verification), checks: [{ name: "one", ok: false }] } }],
  ["zero checks", { ...clone(validReport), final_verification: { ...clone(validReport.final_verification), checks: [] } }],
  ["missing checks", (() => { const r = clone(validReport); delete r.final_verification.checks; return r; })()],
  ["checks not array", { ...clone(validReport), final_verification: { ...clone(validReport.final_verification), checks: "yes" } }],
  ["string ok coercion", { ...clone(validReport), final_verification: { ...clone(validReport.final_verification), checks: [{ name: "one", ok: "true" }] } }],
  ["empty check name", { ...clone(validReport), final_verification: { ...clone(validReport.final_verification), checks: [{ name: "", ok: true }] } }],
  ["record_status omitted", (() => { const r = clone(validReport); delete r.record_status; return r; })()],
  ["record_status invalid", { ...clone(validReport), record_status: "invalid" }],
  ["verification_status failed", { ...clone(validReport), verification_status: "failed" }],
  ["verification_status omitted", (() => { const r = clone(validReport); delete r.verification_status; return r; })()],
  ["recovery_state not verified", { ...clone(validReport), recovery_state: "applied_unverified" }],
  ["recovery_state unknown", { ...clone(validReport), recovery_state: "mystery" }],
  ["schema_version 2", { ...clone(validReport), schema_version: 2 }],
  ["schema_version string", { ...clone(validReport), schema_version: "1" }],
  ["pass false", { ...clone(validReport), final_verification: { ...clone(validReport.final_verification), pass: false } }],
  ["pass string", { ...clone(validReport), final_verification: { ...clone(validReport.final_verification), pass: "true" } }],
  ["preservation false", { ...clone(validReport), final_verification: { ...clone(validReport.final_verification), preservation_verified: false } }],
  ["preservation missing", (() => { const r = clone(validReport); delete r.final_verification.preservation_verified; return r; })()],
  ["final_verification unavailable marker", { ...clone(validReport), final_verification: { available: false, status: "unavailable", reason: "x" } }],
  ["final_verification null", { ...clone(validReport), final_verification: null }],
  ["report null", null],
  ["report array", []],
  ["report string", "verified"],
];
for (const [name, report] of badReports) {
  assert.equal(isCaseVerified(report), false, name);
  assert.notEqual(label(report), "Verified", name);
}
assert.equal(label(null), "No report loaded");
assert.match(label({ ...clone(validReport), schema_version: 2 }), /unsupported report version/);
assert.match(label({ ...clone(validReport), recovery_state: "applied_unverified", verification_status: "failed" }), /Applied but unverified/);
assert.match(label({ ...clone(validReport), recovery_state: "needs_review", verification_status: "failed" }), /Needs review/);

// Raw check validation.
assert.equal(isValidPassingChecks([{ name: "a", ok: true }]), true);
assert.equal(isValidPassingChecks([{ name: "a", ok: true, detail: "d" }]), true);
assert.equal(isValidPassingChecks([]), false);
assert.equal(isValidPassingChecks([{ name: "a", ok: true }, {}]), false);
assert.equal(isValidPassingChecks([{ name: "a", ok: 1 }]), false);
assert.equal(isValidPassingChecks("a"), false);
assert.equal(isValidPassingChecks(null), false);

// Active verification helper.
assert.equal(isActiveVerified({ pass: true, preservation_verified: true, checks: [{ name: "a", ok: true }] }), true);
assert.equal(isActiveVerified({ pass: true, preservation_verified: true, checks: [] }), false);
assert.equal(isActiveVerified({ pass: 1, preservation_verified: true, checks: [{ name: "a", ok: true }] }), false);
assert.equal(isActiveVerified({ pass: true, preservation_verified: true, checks: [{ name: "a", ok: true }, null] }), false);
assert.equal(isActiveVerified(null), false);

// Rust approval readiness.
const rust = { enabled: true, status: "verified", verifier_version: "0.1.0", snapshot_hash: "hash-1", proposal_matches: true, checks: [{ name: "ledger", ok: true }] };
assert.equal(isRustApprovalReady(rust, "hash-1"), true);
assert.equal(isRustApprovalReady({ ...rust, enabled: false }, "hash-1"), false);
assert.equal(isRustApprovalReady((() => { const r: any = { ...rust }; delete r.enabled; return r; })(), "hash-1"), false);
assert.equal(isRustApprovalReady({ ...rust, status: "failed" }, "hash-1"), false);
assert.equal(isRustApprovalReady({ ...rust, verifier_version: "9.9.9" }, "hash-1"), false);
assert.equal(isRustApprovalReady({ ...rust, verifier_version: null }, "hash-1"), false);
assert.equal(isRustApprovalReady(rust, "hash-2"), false);
assert.equal(isRustApprovalReady({ ...rust, snapshot_hash: null }, "hash-1"), false);
assert.equal(isRustApprovalReady(rust, null), false);
assert.equal(isRustApprovalReady({ ...rust, proposal_matches: false }, "hash-1"), false);
assert.equal(isRustApprovalReady({ ...rust, proposal_matches: "true" }, "hash-1"), false);
assert.equal(isRustApprovalReady({ ...rust, checks: [] }, "hash-1"), false);
assert.equal(isRustApprovalReady({ ...rust, checks: [{ name: "ledger", ok: true }, {}] }, "hash-1"), false);
assert.equal(isRustApprovalReady(null, "hash-1"), false);

// Envelope identity.
assert.equal(hasMatchingCaseIdentity(validReport, "CASE-1", "INC-001"), true);
assert.equal(hasMatchingCaseIdentity(validReport, "CASE-2", "INC-001"), false);
assert.equal(hasMatchingCaseIdentity(validReport, "CASE-1", "INC-999"), false);
assert.equal(hasMatchingCaseIdentity({ case_id: "CASE-1" }, "CASE-1", "INC-001"), false);
assert.equal(hasMatchingCaseIdentity(null, "CASE-1", "INC-001"), false);

console.log("check-case-state: all assertions passed");
