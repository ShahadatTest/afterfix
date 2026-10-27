export interface Incident {
  id: string;
  product_id: string;
  description: string;
  status: string;
}

export interface Product {
  product_id: string;
  name: string;
  stock: number;
}

export interface IncidentResponse {
  incident: Incident;
  product: Product;
}

export interface EvidenceEvent {
  id: number;
  product_id: string;
  kind: string;
  qty: number;
  order_id: string | null;
  /** INTEGER Unix seconds from the backend. */
  ts: number;
  note: string | null;
  /** JSON string (nullable) as returned by the backend, not a parsed array. */
  reversed_ids?: string | null;
  repair_proposal_id?: string | null;
}

export interface EvidenceResponse {
  incident_id: string;
  product_id: string;
  snapshot_hash: string;
  events: EvidenceEvent[];
  note: string;
}

export interface PatchResponse {
  incident_id: string;
  patch: string;
}

export interface ResetResponse {
  incident_id: string;
  widget_stock: number;
  gadget_stock: number;
}

export interface CitedStatement {
  text: string;
  evidence_event_ids: number[];
}

export interface TraceStep {
  tool: string;
  event_ids: number[];
  count: number;
}

export interface InvestigationResult {
  status: "proposed" | "needs_review" | string;
  reason?: string;
  proposal?: unknown;
  proposal_id: string | null;
  hypothesis?: string;
  observations: CitedStatement[];
  inferences: CitedStatement[];
  uncertainties: string[];
  explanations_label?: string;
  evidence_event_ids: number[];
  read_event_ids?: number[];
  trace: TraceStep[];
  model?: string;
  usage?: {
    requests: number;
    input_tokens: number;
    output_tokens: number;
    estimated_cost_usd: number;
  };
}

export interface Proposal {
  proposal_id: string;
  status: string;
  product_id: string;
  credit_qty: number;
  reverse_event_ids: number[];
  snapshot_hash: string;
  reason?: string;
}

export interface NamedCheck {
  name: string;
  ok: boolean;
  detail?: string;
}

export interface PreviewResponse {
  proposal: Proposal;
  before_stock: number;
  after_stock: number;
  credit_qty: number;
  diff: {
    stock_delta: number;
    events_added: number;
    /** Event IDs of restocks left untouched by the repair. */
    restocks_untouched: number[];
  };
  preserved_restock_ids: number[];
  /** Baseline list of other products; these are not affected by the repair. */
  unrelated_products: unknown[];
  simulation_checks: NamedCheck[];
  simulation_pass: boolean;
  invariants: NamedCheck[];
  snapshot_match: boolean;
}

export interface ApprovalResponse {
  approval_id: string;
  proposal_id: string;
  snapshot_hash: string;
}

export interface ApplyResponse {
  status: "applied" | "already_applied" | string;
  before_stock?: number;
  after_stock?: number;
  proposal_id?: string;
  [key: string]: unknown;
}

export interface VerifyResponse {
  product_id: string;
  final_stock: number;
  pass: boolean;
  preservation_verified: boolean;
  checks: NamedCheck[];
}
