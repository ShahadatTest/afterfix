// Pure report/check state validation shared by the React components and the
// standalone regression script (frontend/scripts/check-case-state.ts).
// No React, CSS, DOM, network or runtime imports: only structural checks on
// unknown values. Success is never manufactured from filtered/coerced data.

export type RecoveryTone = "ok" | "warn" | "bad" | "muted";
export interface RecoveryLabel { label: string; tone: RecoveryTone; }

export const KNOWN_RUST_VERIFIER_VERSION = "0.1.0";

export function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

// A single RAW check entry is valid only as a plain object with a non-empty
// string name, an exact boolean `ok`, and (if present) a string detail.
// No coercion: "true", 1, missing fields and null are all invalid.
export function isValidCheckEntry(value: unknown): boolean {
  if (!isRecord(value)) return false;
  if (typeof value.name !== "string" || value.name.length === 0) return false;
  if (typeof value.ok !== "boolean") return false;
  if (value.detail !== undefined && typeof value.detail !== "string") return false;
  return true;
}

// Non-empty RAW array where EVERY entry is valid and ok === true exactly.
// Invalid entries are never filtered away to make this pass.
export function isValidPassingChecks(value: unknown): boolean {
  return Array.isArray(value) && value.length > 0 && value.every((c) => isValidCheckEntry(c) && c.ok === true);
}

// Envelope identity: the payload must be an object whose case_id and
// incident_id are strings exactly matching the requested scope.
export function hasMatchingCaseIdentity(value: unknown, caseId: unknown, incidentId: unknown): boolean {
  if (!isRecord(value)) return false;
  if (typeof caseId !== "string" || caseId.length === 0) return false;
  if (typeof incidentId !== "string" || incidentId.length === 0) return false;
  return value.case_id === caseId && value.incident_id === incidentId;
}

// Authoritative saved-report verification. Every status/flag must match
// exactly and the raw final-verification checks must be non-empty and all
// valid/passing. Any missing, malformed, null, false or contradictory
// field makes this false.
export function isCaseVerified(report: unknown): boolean {
  if (!isRecord(report)) return false;
  if (report.schema_version !== 1) return false;
  if (report.record_status !== "ok") return false;
  if (report.verification_status !== "passed") return false;
  if (report.recovery_state !== "verified") return false;
  const fv: unknown = report.final_verification;
  if (!isRecord(fv)) return false;
  if (fv.pass !== true) return false;
  if (fv.preservation_verified !== true) return false;
  return isValidPassingChecks(fv.checks);
}

export function caseRecoveryLabel(report: unknown): RecoveryLabel {
  if (!isRecord(report)) return { label: "No report loaded", tone: "muted" };
  if (report.schema_version !== 1) return { label: "Needs review — unsupported report version", tone: "bad" };
  if (report.record_status !== "ok") return { label: "Needs review — invalid or unverified saved record", tone: "bad" };
  if (isCaseVerified(report)) return { label: "Verified", tone: "ok" };
  if (report.recovery_state === "verified") return { label: "Needs review — verification evidence incomplete or contradictory", tone: "warn" };
  if (report.recovery_state === "applied_unverified") return { label: "Applied but unverified / needs review", tone: "warn" };
  if (report.recovery_state === "needs_review") return { label: "Needs review", tone: "warn" };
  if (report.recovery_state === "approved") return { label: "Approved — not yet applied", tone: "muted" };
  if (report.recovery_state === "proposed") return { label: "Proposed — not yet approved", tone: "muted" };
  const state = typeof report.recovery_state === "string" && report.recovery_state ? report.recovery_state : "unavailable";
  return { label: `Needs review — unknown recovery state (${state})`, tone: "warn" };
}

// Active-flow verification (VerifyResponse-shaped unknown): exact true
// booleans plus a non-empty raw array of valid, passing checks.
export function isActiveVerified(result: unknown): boolean {
  if (!isRecord(result)) return false;
  if (result.pass !== true) return false;
  if (result.preservation_verified !== true) return false;
  return isValidPassingChecks(result.checks);
}

// Rust approval readiness for the active preview. Backend remains the final
// authority; this only gates the local approve action. Requires enabled
// exactly true, status exactly "verified", a known verifier version, a
// snapshot hash exactly matching the actual preview proposal snapshot,
// proposal_matches exactly true, and non-empty all-valid passing checks.
export function isRustApprovalReady(rust: unknown, previewSnapshotHash: unknown): boolean {
  if (!isRecord(rust)) return false;
  if (rust.enabled !== true) return false;
  if (rust.status !== "verified") return false;
  if (rust.verifier_version !== KNOWN_RUST_VERIFIER_VERSION) return false;
  if (typeof previewSnapshotHash !== "string" || previewSnapshotHash.length === 0) return false;
  if (rust.snapshot_hash !== previewSnapshotHash) return false;
  if (rust.proposal_matches !== true) return false;
  return isValidPassingChecks(rust.checks);
}
