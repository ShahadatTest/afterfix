export interface AuthMeResponse {
  actor_id: "operator" | "viewer";
  role: "operator" | "viewer";
  can_reset: boolean;
  workspace_mode?: "connected";
}
export interface Incident { id: string; product_id: string; description: string; status: string; }
export interface Product { product_id: string; name: string; stock: number; }
export interface IncidentResponse { incident: Incident; product: Product; }
export interface EvidenceEvent {
  id: number; product_id: string; kind: string; qty: number; order_id: string | null;
  ts: number; note: string | null; reversed_ids?: string | null; repair_proposal_id?: string | null;
}
export interface EvidenceResponse { incident_id: string; product_id: string; snapshot_hash: string; events: EvidenceEvent[]; note: string; }
export interface PatchResponse { incident_id: string; patch: string; }
export interface ResetResponse { incident_id: string; widget_stock: number; gadget_stock: number; }
export interface CitedStatement { text: string; evidence_event_ids: number[]; }
export interface TraceStep { tool: string; event_ids: number[]; count: number; }
export interface Usage {
  requests?: number | null; input_tokens?: number | null; output_tokens?: number | null;
  total_tokens?: number | null; estimated_cost_usd?: number | null; [key: string]: unknown;
}
export interface InvestigationResult {
  status: "proposed" | "needs_review" | string;
  reason?: string; stop_category?: string | null;
  proposal?: unknown; proposal_id: string | null;
  case_id?: string | null;
  case_recording_status?: "recorded" | "unavailable" | string | null;
  hypothesis?: string;
  observations: CitedStatement[]; inferences: CitedStatement[]; uncertainties: string[];
  explanations_label?: string; evidence_event_ids: number[]; read_event_ids?: number[];
  trace: TraceStep[]; model?: string | null; usage?: Usage | null;
}
export interface Proposal {
  proposal_id: string; status: string; product_id: string; credit_qty: number;
  reverse_event_ids: number[]; snapshot_hash: string; reason?: string | null;
  incident_id?: string; created_at?: number;
}
export interface NamedCheck { name: string; ok: boolean; detail?: string; }
export interface RustVerification {
  enabled?: boolean; status: string; verifier_version?: string | null; snapshot_hash?: string | null;
  checks?: NamedCheck[] | unknown; ledger_actual?: number | null; expected_stock?: number | null;
  outstanding_credit?: number | null; reverse_event_ids?: number[]; proposal_matches?: boolean | null;
  error_code?: string | null; [key: string]: unknown;
}
export interface PreviewResponse {
  proposal: Proposal; status?: string;
  before_stock: number; after_stock: number; credit_qty: number;
  diff: { stock_delta: number; events_added: number; restocks_untouched: number[]; };
  preserved_restock_ids: number[]; unrelated_products: unknown[];
  simulation_checks: NamedCheck[]; simulation_pass: boolean; invariants: NamedCheck[];
  snapshot_match: boolean; rust_verification?: RustVerification | null;
}
export interface ApprovalResponse { approval_id: string; proposal_id: string; snapshot_hash: string; actor_id?: string; }
export interface ApplyResponse { status: "applied" | "already_applied" | string; before_stock?: number; after_stock?: number; proposal_id?: string; [key: string]: unknown; }
export interface VerifyResponse { product_id: string; final_stock: number; pass: boolean; preservation_verified: boolean; checks: NamedCheck[]; }

/* Durable case reports (docs/CASE_REPORTS.md, schema version 1) */
export interface CaseSummary {
  case_id: string; incident_id: string; product_id: string; proposal_id: string | null;
  recorded_actor_id?: string | null; actor_id?: string | null; created_at: number;
  status: string; stop_category?: string | null; data_label: string; record_status?: string;
}
export interface CaseListResponse { incident_id: string; cases: CaseSummary[]; }
export interface UnavailableMarker { available: false; status: "unavailable" | string; reason: string; [key: string]: unknown; }
export type FinalVerification = VerifyResponse;
export interface CaseBefore {
  available?: boolean; failure_reason?: string | null; reason?: string | null; capture_id?: string | null;
  incident?: Incident | null; product?: Product | null; events?: EvidenceEvent[]; event_ids?: number[];
  patch?: string | null; raw_patch?: string | null; fingerprint?: string | null; snapshot_hash?: string | null;
  evidence_binding?: unknown; binding?: unknown; [key: string]: unknown;
}
export interface CaseCurrent {
  available?: boolean; status?: string; reason?: string | null;
  incident?: Incident | null; product?: Product | null; events?: EvidenceEvent[];
  fingerprint?: string | null; snapshot_hash?: string | null;
  proposal?: Proposal | null; current_proposal?: Proposal | null;
  approvals?: unknown[]; audit?: unknown[]; repair_events?: EvidenceEvent[]; [key: string]: unknown;
}
export interface ModelClaims {
  status?: string; reason?: string | null; stop_category?: string | null; hypothesis?: string | null;
  observations?: CitedStatement[]; inferences?: CitedStatement[]; uncertainties?: string[];
  read_event_ids?: number[]; selected_event_ids?: number[]; evidence_event_ids?: number[];
  trace?: TraceStep[] | unknown[]; model?: string | null; usage?: Usage | null;
  proposal_id?: string | null; scope?: unknown; [key: string]: unknown;
}
export interface CaseReport {
  schema_version: number; case_id: string; data_label: string; incident_id: string; product_id: string;
  proposal_id: string | null; recorded_actor_id?: string | null; actor_id?: string | null; created_at: number;
  record_status: "ok" | "invalid" | string; recovery_state: string; verification_status: string;
  before: CaseBefore | null; investigation?: ModelClaims | null; model_claims?: ModelClaims | null;
  initial_proposal: (Proposal & Record<string, unknown>) | null;
  initial_preview: PreviewResponse | UnavailableMarker | null;
  current: CaseCurrent | UnavailableMarker | null; current_proposal?: Proposal | null;
  approvals?: unknown[]; audit?: unknown[];
  final_verification: FinalVerification | UnavailableMarker | null;
  [key: string]: unknown;
}
