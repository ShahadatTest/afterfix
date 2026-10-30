// Pure bounded parsers for Scenario Lab API responses. No React/DOM/network/env.
export const SCENARIO_IDS = ["duplicate_order", "missing_opening", "stale_snapshot"] as const;
export type ScenarioId = (typeof SCENARIO_IDS)[number];
export const CANONICAL_INCIDENT_ID = "INC-001";
export const CANONICAL_PRODUCT_ID = "WIDGET-1";
export const CANONICAL_DATA_LABEL = "Synthetic Scenario Lab";
export const LOAD_CONFIRMATION = "RESET_SYNTHETIC_SCENARIO";
export const ADVANCE_CONFIRMATION = "ADD_SYNTHETIC_RESTOCK";
export const MAX_PROPOSAL_ID_LENGTH = 200;

export interface ScenarioCatalogueItem { id: ScenarioId; title: string; description: string; can_advance: boolean }
export interface ScenarioCatalogue { enabled: true; data_label: string; can_load: boolean; scenarios: ScenarioCatalogueItem[] }
export interface LabLoad { scenario_id: ScenarioId; load_id: string; incident_id: string; product_id: string; snapshot_hash: string; data_label: string; widget_stock: number; gadget_stock: number }
export interface LabAdvance { scenario_id: "stale_snapshot"; load_id: string; snapshot_hash: string; incident_id: string; product_id: string; data_label: string; stock: number; event_id: number }
export interface LabSession { scenarioId: ScenarioId; loadId: string; snapshotHash: string; incidentId: string; productId: string; dataLabel: string; widgetStock: number | null; gadgetStock: number | null; stock: number | null; advanced: boolean; advanceEventId: number | null }

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}
function boundedString(v: unknown, max: number): v is string {
  return typeof v === "string" && v.length > 0 && v.length <= max && v.trim().length > 0;
}
function isProposalId(v: unknown): v is string {
  return boundedString(v, MAX_PROPOSAL_ID_LENGTH);
}
function isExactBoolean(v: unknown): v is boolean {
  return typeof v === "boolean";
}
export function isKnownScenarioId(v: unknown): v is ScenarioId {
  return typeof v === "string" && (SCENARIO_IDS as readonly string[]).includes(v);
}
export function isLowerUuid(v: unknown): v is string {
  return typeof v === "string" && v.length === 36 && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
}
export function isSnapshotHash(v: unknown): v is string {
  return typeof v === "string" && v.length === 64 && /^[0-9a-f]{64}$/.test(v);
}
export function isNonnegativeSafeInt(v: unknown): v is number {
  return typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
}
export function isPositiveSafeInt(v: unknown): v is number {
  return typeof v === "number" && Number.isSafeInteger(v) && v > 0;
}
function isNullableNonnegativeSafeInt(v: unknown): boolean {
  return v === null || isNonnegativeSafeInt(v);
}
function canonicalScope(v: Record<string, unknown>): boolean {
  return v.incident_id === CANONICAL_INCIDENT_ID && v.product_id === CANONICAL_PRODUCT_ID && v.data_label === CANONICAL_DATA_LABEL;
}

/**
 * Defensively validate an in-memory session at runtime. Sessions are typed,
 * but callers may hold stale or externally forged objects cast to LabSession;
 * never trust the static type alone and never throw on malformed input.
 */
function isValidLabSession(v: unknown): v is LabSession {
  if (!isRecord(v)) return false;
  if (!isKnownScenarioId(v.scenarioId)) return false;
  if (!isLowerUuid(v.loadId) || !isSnapshotHash(v.snapshotHash)) return false;
  if (v.incidentId !== CANONICAL_INCIDENT_ID) return false;
  if (v.productId !== CANONICAL_PRODUCT_ID) return false;
  if (v.dataLabel !== CANONICAL_DATA_LABEL) return false;
  if (!isNullableNonnegativeSafeInt(v.widgetStock)) return false;
  if (!isNullableNonnegativeSafeInt(v.gadgetStock)) return false;
  if (!isNullableNonnegativeSafeInt(v.stock)) return false;
  if (!isExactBoolean(v.advanced)) return false;
  if (v.advanced) {
    if (!isPositiveSafeInt(v.advanceEventId)) return false;
  } else if (v.advanceEventId !== null) {
    return false;
  }
  return true;
}
function isStaleUnadvancedSession(v: unknown): v is LabSession {
  return isValidLabSession(v) && v.scenarioId === "stale_snapshot" && v.advanced === false;
}

export function parseCatalogue(v: unknown): ScenarioCatalogue | null {
  if (!isRecord(v) || v.enabled !== true || v.data_label !== CANONICAL_DATA_LABEL || !isExactBoolean(v.can_load)) return null;
  if (!Array.isArray(v.scenarios) || v.scenarios.length !== SCENARIO_IDS.length) return null;
  const seen = new Set<string>();
  const scenarios: ScenarioCatalogueItem[] = [];
  for (const item of v.scenarios) {
    if (!isRecord(item) || !isKnownScenarioId(item.id) || seen.has(item.id)) return null;
    if (!boundedString(item.title, 120) || !boundedString(item.description, 500) || !isExactBoolean(item.can_advance)) return null;
    if (item.can_advance !== (item.id === "stale_snapshot")) return null;
    seen.add(item.id);
    scenarios.push({ id: item.id, title: item.title, description: item.description, can_advance: item.can_advance });
  }
  if (!SCENARIO_IDS.every((id) => seen.has(id))) return null;
  return { enabled: true, data_label: CANONICAL_DATA_LABEL, can_load: v.can_load, scenarios };
}

export function parseLoadResponse(v: unknown, requested: unknown): LabLoad | null {
  if (!isRecord(v) || !isKnownScenarioId(requested) || v.scenario_id !== requested) return null;
  if (!isLowerUuid(v.load_id) || !isSnapshotHash(v.snapshot_hash) || !canonicalScope(v)) return null;
  if (!isNonnegativeSafeInt(v.widget_stock) || !isNonnegativeSafeInt(v.gadget_stock)) return null;
  return { scenario_id: requested, load_id: v.load_id, incident_id: CANONICAL_INCIDENT_ID, product_id: CANONICAL_PRODUCT_ID, snapshot_hash: v.snapshot_hash, data_label: CANONICAL_DATA_LABEL, widget_stock: v.widget_stock, gadget_stock: v.gadget_stock };
}

export function createLabSession(load: LabLoad | null): LabSession | null {
  if (!isRecord(load)) return null;
  // Re-validate rather than trusting a possibly forged LabLoad-shaped object.
  const parsed = parseLoadResponse(load, load.scenario_id);
  if (!parsed) return null;
  return { scenarioId: parsed.scenario_id, loadId: parsed.load_id, snapshotHash: parsed.snapshot_hash, incidentId: parsed.incident_id, productId: parsed.product_id, dataLabel: parsed.data_label, widgetStock: parsed.widget_stock, gadgetStock: parsed.gadget_stock, stock: null, advanced: false, advanceEventId: null };
}

export function parseAdvanceResponse(v: unknown, session: LabSession | null, proposalId: unknown, expectedHash: unknown): LabAdvance | null {
  if (!isRecord(v) || !isStaleUnadvancedSession(session)) return null;
  if (!isProposalId(proposalId)) return null;
  if (!isSnapshotHash(expectedHash) || expectedHash !== session.snapshotHash) return null;
  if (v.scenario_id !== "stale_snapshot" || v.load_id !== session.loadId || !canonicalScope(v)) return null;
  if (!isSnapshotHash(v.snapshot_hash) || v.snapshot_hash === expectedHash) return null;
  if (!isNonnegativeSafeInt(v.stock) || !isPositiveSafeInt(v.event_id)) return null;
  return { scenario_id: "stale_snapshot", load_id: session.loadId, snapshot_hash: v.snapshot_hash, incident_id: CANONICAL_INCIDENT_ID, product_id: CANONICAL_PRODUCT_ID, data_label: CANONICAL_DATA_LABEL, stock: v.stock, event_id: v.event_id };
}

export function advanceSession(session: LabSession, advance: LabAdvance): LabSession | null {
  // Validate both operands fully: a forged advance (or session) cast to the
  // static types must not be spread into a trusted advanced session.
  if (!isStaleUnadvancedSession(session)) return null;
  if (!isRecord(advance)) return null;
  if (advance.scenario_id !== "stale_snapshot") return null;
  if (advance.load_id !== session.loadId) return null;
  if (advance.incident_id !== CANONICAL_INCIDENT_ID || advance.incident_id !== session.incidentId) return null;
  if (advance.product_id !== CANONICAL_PRODUCT_ID || advance.product_id !== session.productId) return null;
  if (advance.data_label !== CANONICAL_DATA_LABEL || advance.data_label !== session.dataLabel) return null;
  if (!isSnapshotHash(advance.snapshot_hash) || advance.snapshot_hash === session.snapshotHash) return null;
  if (!isNonnegativeSafeInt(advance.stock) || !isPositiveSafeInt(advance.event_id)) return null;
  return { ...session, snapshotHash: advance.snapshot_hash, stock: advance.stock, advanced: true, advanceEventId: advance.event_id };
}

export function hasAdvanceBinding(session: LabSession | null, proposalId: unknown, snapshotHash: unknown, validPreview: boolean, hasAppliedRepair: boolean): boolean {
  if (!isStaleUnadvancedSession(session)) return false;
  if (validPreview !== true || hasAppliedRepair !== false) return false;
  if (!isProposalId(proposalId)) return false;
  if (!isSnapshotHash(snapshotHash) || snapshotHash !== session.snapshotHash) return false;
  return true;
}

/**
 * Canonical readiness for the synthetic advance action.
 *
 * `validPreview` is the caller's existing full mandatory Rust-ready check;
 * this helper only requires that it is exactly boolean true and binds the
 * preview to this session. It confers no approval authority of its own.
 * The nested proposal record itself must carry a bounded nonblank
 * proposal_id, the canonical incident/product scope, a snapshot_hash equal
 * to the session's current valid hash, and status `proposed` or `approved`.
 * Optional preview-level duplicates of those fields are never a substitute
 * for a missing nested proposal field; when present, they must agree with
 * the corresponding nested/canonical/session value.
 */
export function hasCanonicalAdvanceBinding(session: LabSession | null, preview: unknown, validPreview: boolean, hasAppliedRepair: boolean): boolean {
  if (!isStaleUnadvancedSession(session)) return false;
  if (validPreview !== true || hasAppliedRepair !== false) return false;
  if (!isRecord(preview) || !isRecord(preview.proposal)) return false;
  const proposal = preview.proposal;
  if (proposal.status !== "proposed" && proposal.status !== "approved") return false;
  if (!isProposalId(proposal.proposal_id)) return false;
  if (proposal.incident_id !== CANONICAL_INCIDENT_ID) return false;
  if (proposal.product_id !== CANONICAL_PRODUCT_ID) return false;
  if (!isSnapshotHash(proposal.snapshot_hash) || proposal.snapshot_hash !== session.snapshotHash) return false;
  if (session.incidentId !== CANONICAL_INCIDENT_ID || session.productId !== CANONICAL_PRODUCT_ID) return false;
  if (Object.prototype.hasOwnProperty.call(preview, "proposal_id") && preview.proposal_id !== proposal.proposal_id) return false;
  if (Object.prototype.hasOwnProperty.call(preview, "incident_id") && preview.incident_id !== CANONICAL_INCIDENT_ID) return false;
  if (Object.prototype.hasOwnProperty.call(preview, "product_id") && preview.product_id !== CANONICAL_PRODUCT_ID) return false;
  if (Object.prototype.hasOwnProperty.call(preview, "snapshot_hash") && preview.snapshot_hash !== session.snapshotHash) return false;
  return true;
}
