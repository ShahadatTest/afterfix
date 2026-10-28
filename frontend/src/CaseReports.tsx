import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet } from "./api";
import { caseRecoveryLabel, hasMatchingCaseIdentity, isRecord } from "./caseState";
import type { CaseListResponse, CaseReport, CaseSummary, NamedCheck } from "./types";
import "./cases.css";

type Rec = Record<string, unknown>;
function isRec(v: unknown): v is Rec { return isRecord(v); }
function arr(v: unknown): unknown[] { return Array.isArray(v) ? v : []; }
function str(v: unknown): string | null { return typeof v === "string" && v ? v : null; }
function num(v: unknown): number | null { return typeof v === "number" && Number.isFinite(v) ? v : null; }
function bool(v: unknown): boolean | null { return typeof v === "boolean" ? v : null; }
function numText(v: unknown): string { const n = num(v); return n === null ? "unknown / not reported" : String(n); }
function timeText(v: unknown): string { const n = num(v); return n === null ? "unknown" : new Date(n * 1000).toLocaleString(); }
function idsText(v: unknown): string { const a = arr(v).map((x) => num(x)).filter((x): x is number => x !== null); return a.length ? a.map((x) => `#${x}`).join(", ") : "none reported"; }
// Display-only filtering: malformed entries are omitted from display lists,
// but success/verified decisions never use this (see caseState.ts, which
// validates the RAW array). This helper must never manufacture success.
function checksOf(v: unknown): NamedCheck[] {
  return arr(v).flatMap((c) => {
    if (!isRec(c) || typeof c.name !== "string" || typeof c.ok !== "boolean") return [];
    return [{ name: c.name, ok: c.ok, detail: typeof c.detail === "string" ? c.detail : undefined }];
  });
}
function boundedJson(v: unknown): string {
  try { const s = JSON.stringify(v, null, 2) ?? "unavailable"; return s.length > 12000 ? `${s.slice(0, 12000)}\n… (truncated for display; download has the exact server report)` : s; }
  catch { return "unavailable"; }
}
function safeFilePart(id: string): string { return id.replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "") || "case"; }

export { caseRecoveryLabel };

interface Props { incidentId: string; refreshSignal: number; clearSignal: number; focusCaseId: string | null; resetting?: boolean; }

export default function CaseReports({ incidentId, refreshSignal, clearSignal, focusCaseId, resetting = false }: Props) {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [report, setReport] = useState<CaseReport | null>(null);
  const [listState, setListState] = useState<"loading" | "ready" | "error">("loading");
  const [reportState, setReportState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const seqRef = useRef(0);
  const downloadSeqRef = useRef(0);
  const selectedRef = useRef<string | null>(null);
  const incidentRef = useRef(incidentId);
  incidentRef.current = incidentId;
  const focusRef = useRef<string | null>(focusCaseId);
  focusRef.current = focusCaseId;
  const resettingRef = useRef(resetting);
  resettingRef.current = resetting;
  const prevResettingRef = useRef(resetting);

  const current = useCallback((seq: number, caseId?: string) =>
    seq === seqRef.current && !resettingRef.current && incidentRef.current === incidentId && (caseId === undefined || selectedRef.current === caseId),
  [incidentId]);

  const clearVisible = useCallback(() => {
    selectedRef.current = null;
    setCases([]); setSelectedId(null); setReport(null);
    setReportState("idle"); setError(null); setDownloadError(null); setDownloading(false);
  }, []);

  const fetchReport = useCallback(async (caseId: string, seq: number) => {
    setReport(null); setReportState("loading"); setDownloadError(null);
    try {
      const r: unknown = await apiGet<unknown>(`/api/cases/${encodeURIComponent(caseId)}/report`);
      if (!current(seq, caseId)) return;
      if (!isRec(r) || !hasMatchingCaseIdentity(r, caseId, incidentId)) {
        setReport(null); setReportState("error");
        setError("The saved report did not match the selected case and incident, or has an unexpected shape. It was not displayed as the requested case.");
        return;
      }
      setReport(r as CaseReport); setReportState("ready");
    } catch (e) {
      if (!current(seq, caseId)) return;
      setReport(null); setReportState("error"); setError(e instanceof Error ? e.message : "Could not load the saved report.");
    }
  }, [current, incidentId]);

  const loadCases = useCallback(async (preferred: string | null) => {
    if (resettingRef.current) return;
    const seq = ++seqRef.current;
    // A refresh must not leave a stale (possibly green) report visible.
    setListState("loading"); setError(null); setDownloadError(null); setReport(null); setReportState("idle");
    try {
      const data: unknown = await apiGet<unknown>(`/api/incidents/${encodeURIComponent(incidentId)}/cases`);
      if (!current(seq)) return;
      if (!isRec(data) || str(data.incident_id) !== incidentId || !Array.isArray(data.cases)) {
        selectedRef.current = null; setCases([]); setSelectedId(null); setReport(null); setListState("error"); setReportState("idle");
        setError("The saved-case list did not match this incident or has an unexpected shape.");
        return;
      }
      const list = data.cases.filter((c): c is CaseSummary => isRec(c) && typeof c.case_id === "string" && c.case_id.length > 0);
      setCases(list); setListState("ready");
      const next = preferred && list.some((c) => c.case_id === preferred) ? preferred : selectedRef.current && list.some((c) => c.case_id === selectedRef.current) ? selectedRef.current : list[0]?.case_id ?? null;
      selectedRef.current = next; setSelectedId(next);
      if (next) void fetchReport(next, seq);
      else { setReport(null); setReportState("idle"); }
    } catch (e) {
      if (!current(seq)) return;
      // List failure clears selection/report so no success from an old ID survives.
      selectedRef.current = null; setCases([]); setSelectedId(null); setReport(null); setListState("error"); setReportState("idle");
      setError(e instanceof Error ? e.message : "Could not load saved cases.");
    }
  }, [current, fetchReport, incidentId]);

  // Initial / incident / refresh load. No first-run skip refs: StrictMode's
  // setup-cleanup-setup simply starts a load, cleanup invalidates it, and the
  // second setup starts the one effective current load. Duplicate development
  // GETs may occur but stale results are discarded; no POST is issued here.
  useEffect(() => {
    void loadCases(focusRef.current);
    return () => { seqRef.current += 1; downloadSeqRef.current += 1; };
  }, [incidentId, refreshSignal, loadCases]);

  // Post-reset outcome signal: history was already invalidated at reset start
  // via `resetting`; once the outcome is known, reload the actual list.
  useEffect(() => {
    if (clearSignal === 0) return;
    seqRef.current += 1; downloadSeqRef.current += 1; clearVisible(); setListState("loading");
    void loadCases(null);
    return () => { seqRef.current += 1; };
  }, [clearSignal, clearVisible, loadCases]);

  // Reset start/end. At start: invalidate generations, suspend loads/actions
  // and hide pre-reset history/report. At end: reload actual current data, so
  // a failed reset keeps the (unchanged) server data and a successful reset
  // shows the cleared state. Pre-reset GETs resolving during reset are dropped
  // because `current()` and load guards check resettingRef.
  useEffect(() => {
    const was = prevResettingRef.current;
    prevResettingRef.current = resetting;
    if (resetting) {
      seqRef.current += 1; downloadSeqRef.current += 1; clearVisible(); setListState("loading");
      return;
    }
    if (was) void loadCases(focusRef.current);
  }, [resetting, clearVisible, loadCases]);

  function onSelect(id: string) {
    if (resetting) return;
    const seq = ++seqRef.current; downloadSeqRef.current += 1;
    selectedRef.current = id || null; setSelectedId(id || null); setReport(null); setError(null); setDownloadError(null); setDownloading(false);
    if (id) void fetchReport(id, seq); else setReportState("idle");
  }
  function refresh() { if (!resetting) void loadCases(selectedRef.current); }

  async function download() {
    const id = selectedRef.current;
    if (!id || downloading || resetting) return;
    const dseq = ++downloadSeqRef.current;
    const seq = seqRef.current;
    const forIncident = incidentId;
    setDownloading(true); setDownloadError(null);
    try {
      const fresh: unknown = await apiGet<unknown>(`/api/cases/${encodeURIComponent(id)}/report`);
      // Re-verify generation AND envelope identity BEFORE creating Blob/link.
      if (dseq !== downloadSeqRef.current || seq !== seqRef.current || selectedRef.current !== id || incidentRef.current !== forIncident || resettingRef.current) return;
      if (!isRec(fresh) || !hasMatchingCaseIdentity(fresh, id, forIncident)) {
        setDownloadError("Download stopped: the server response did not match the selected case and incident.");
        return;
      }
      // Exact, complete server JSON — never locally fabricated.
      const url = URL.createObjectURL(new Blob([JSON.stringify(fresh, null, 2)], { type: "application/json" }));
      try { const a = document.createElement("a"); a.href = url; a.download = `afterfix-case-${safeFilePart(id)}.json`; a.click(); }
      finally { URL.revokeObjectURL(url); }
    } catch (e) {
      if (dseq === downloadSeqRef.current && seq === seqRef.current && selectedRef.current === id && incidentRef.current === forIncident && !resettingRef.current) {
        setDownloadError(e instanceof Error ? e.message : "Download could not be completed.");
      }
    } finally {
      if (dseq === downloadSeqRef.current) setDownloading(false);
    }
  }

  const recovery = caseRecoveryLabel(report);
  const unknownVersion = !!report && report.schema_version !== 1;
  const recordInvalid = !!report && report.record_status === "invalid";
  const before = !unknownVersion && isRec(report?.before) ? report.before : null;
  const currentSection = !unknownVersion && isRec(report?.current) ? report.current : null;
  const claims = (!unknownVersion ? (isRec(report?.model_claims) ? report.model_claims : isRec(report?.investigation) ? report.investigation : null) : null) as Rec | null;
  const preview = !unknownVersion && isRec(report?.initial_preview) ? report.initial_preview : null;
  const previewUnavailable = !!preview && preview.available === false;
  const usablePreview = preview && !previewUnavailable ? preview : null;
  // Nested proposal is narrowed with a runtime object guard before any field use.
  const previewProposal: Rec | null = isRec(usablePreview?.proposal) ? usablePreview.proposal : null;
  const fv = !unknownVersion && isRec(report?.final_verification) ? report.final_verification : null;
  const fvUnavailable = !!fv && fv.available === false;
  const fvChecks = fv && !fvUnavailable ? checksOf(fv.checks) : [];
  const rust = isRec(usablePreview?.rust_verification) ? usablePreview.rust_verification : null;
  const approvals = arr(report?.approvals ?? currentSection?.approvals);
  const audit = arr(report?.audit ?? currentSection?.audit);
  const usage = isRec(claims?.usage) ? claims.usage : null;
  const beforeProduct = isRec(before?.product) ? before.product : null;
  const currentProduct = isRec(currentSection?.product) ? currentSection.product : null;
  const beforeIncident = isRec(before?.incident) ? before.incident : null;
  const patchText = str(before?.raw_patch) ?? str(before?.patch);
  const initialProposalId = isRec(report?.initial_proposal) ? str(report.initial_proposal.proposal_id) : null;

  return (
    <section className="panel cases-panel" aria-labelledby="cases-title">
      <div className="panel-head"><div><span className="eyebrow">Saved cases</span><h2 id="cases-title">History review (read-only)</h2>
      <p className="muted">Saved cases are read-only. To start a new repair, run a fresh investigation.</p></div></div>
      <div className="cases-controls">
        <label className="cases-select-label" htmlFor="case-select">Saved case</label>
        <select id="case-select" className="cases-select" value={selectedId ?? ""} onChange={(e) => onSelect(e.target.value)} disabled={resetting || listState === "loading" || cases.length === 0}>
          {cases.length === 0 ? <option value="">No saved cases</option> : cases.map((c) => <option key={c.case_id} value={c.case_id}>{c.case_id} · {timeText(c.created_at)} · original status: {str(c.status) ?? "unknown"}{c.record_status === "invalid" ? " · invalid" : ""}</option>)}
        </select>
        <button type="button" className="btn" onClick={refresh} disabled={resetting || listState === "loading"}>Refresh cases</button>
        <button type="button" className="btn btn-primary" onClick={download} disabled={resetting || !selectedId || downloading || reportState === "loading"}>{downloading ? "Preparing download…" : "Download full server report (JSON)"}</button>
      </div>
      {resetting && <p className="muted" role="status">Reset in progress — saved-case history is suspended and will reload when the reset finishes.</p>}
      {listState === "loading" && !resetting && <p className="muted" role="status">Loading saved cases…</p>}
      {listState === "error" && <div className="banner error" role="alert">{error ?? "Saved cases are unavailable."} <button type="button" className="btn btn-small" onClick={refresh}>Try refresh</button></div>}
      {listState === "ready" && cases.length === 0 && <p className="muted">No saved cases for this incident yet. A completed investigation that records successfully will appear here.</p>}
      {listState === "ready" && cases.length > 0 && reportState === "loading" && <p className="muted" role="status">Loading full report…</p>}
      {reportState === "error" && <div className="banner warn" role="alert"><strong>Report needs review / unavailable.</strong> {error ?? "The full report could not be loaded."}</div>}
      {downloadError && <div className="banner error" role="alert"><strong>Download failed.</strong> {downloadError}</div>}

      {report && reportState === "ready" && (
        <div className="stack cases-body">
          <div className={`banner ${recovery.tone === "ok" ? "ok" : recovery.tone === "bad" ? "error" : "warn"}`} role="status"><strong>{recovery.label}.</strong> Historical report — read-only. The summary shows the original investigation outcome; open the report for the current recovery state.</div>
          {unknownVersion && <div className="banner error" role="alert">Unsupported report version ({String(report.schema_version)}). Only safe metadata and bounded raw data are shown; version-1 fields are not assumed and this is not a success.</div>}
          {recordInvalid && <div className="banner error" role="alert">This saved record is invalid; unavailable sections are not reconstructed.</div>}
          <dl className="mini grid-info cases-grid">
            <div><dt>Case ID</dt><dd className="mono cases-wrap">{str(report.case_id) ?? "unknown"}</dd></div>
            <div><dt>Incident ID</dt><dd className="mono cases-wrap">{str(report.incident_id) ?? "unknown"}</dd></div>
            <div><dt>Recorded</dt><dd>{timeText(report.created_at)}</dd></div>
            <div><dt>Actor</dt><dd className="mono">{str(report.recorded_actor_id) ?? str(report.actor_id) ?? "unknown"}</dd></div>
            <div><dt>Data label</dt><dd>{str(report.data_label) ?? "unknown"}</dd></div>
            <div><dt>Schema version</dt><dd>{numText(report.schema_version)}</dd></div>
            <div><dt>Record status</dt><dd>{str(report.record_status) ?? "unavailable"}</dd></div>
            <div><dt>Verification status</dt><dd>{str(report.verification_status) ?? "unavailable"}</dd></div>
            <div><dt>Proposal</dt><dd className="mono cases-wrap">{str(report.proposal_id) ?? initialProposalId ?? "none"}</dd></div>
            <div><dt>Product</dt><dd className="mono">{str(report.product_id) ?? "unknown"}</dd></div>
            {!unknownVersion && <div><dt>DB incident status (before capture)</dt><dd>{str(beforeIncident?.status) ?? "unavailable"}</dd></div>}
          </dl>
          {unknownVersion ? (
            <details className="cases-details" open><summary>Raw bounded report data (text only)</summary><pre className="cases-pre">{boundedJson(report)}</pre></details>
          ) : (<>
          <div className="diff-cards">
            <div className="diff-card"><span>Original BEFORE stock</span><strong>{numText(beforeProduct?.stock)}</strong><small>immutable server capture</small></div>
            <div className="diff-card"><span>Original proposed after</span><strong>{numText(usablePreview?.after_stock)}</strong><small>original preview, unchanged by apply</small></div>
            <div className="diff-card after"><span>Current stock</span><strong>{numText(currentProduct?.stock)}</strong><small>from current report section</small></div>
          </div>
          <div className="grid2">
            <section className="subpanel"><h4>Reason / stop category</h4><dl className="mini"><div><dt>Reason</dt><dd>{str(claims?.reason) ?? (isRec(report.initial_proposal) ? str(report.initial_proposal.reason) : null) ?? "unknown"}</dd></div><div><dt>Stop category</dt><dd>{str(claims?.stop_category) ?? "unknown / not reported"}</dd></div><div><dt>Original investigation status</dt><dd>{str(claims?.status) ?? "unknown"}</dd></div></dl></section>
            <section className="subpanel"><h4>Model usage (actual, when known)</h4>{usage ? <dl className="mini"><div><dt>Model</dt><dd className="mono cases-wrap">{str(claims?.model) ?? "unknown"}</dd></div><div><dt>Requests</dt><dd>{numText(usage.requests)}</dd></div><div><dt>Input tokens</dt><dd>{numText(usage.input_tokens)}</dd></div><div><dt>Output tokens</dt><dd>{numText(usage.output_tokens)}</dd></div><div><dt>Total tokens</dt><dd>{numText(usage.total_tokens)}</dd></div><div><dt>Estimated cost</dt><dd>{num(usage.estimated_cost_usd) === null ? "unknown / not reported" : `$${num(usage.estimated_cost_usd)?.toFixed(5)}`}</dd></div></dl> : <p className="muted">Usage unknown / not reported.</p>}</section>
          </div>
          <section className="subpanel"><h4>Model hypothesis (claim)</h4><p>{str(claims?.hypothesis) ?? "Not reported."}</p><p className="label-note">Model explanations are claims, distinct from server-captured events and actual model-read IDs.</p>
            <dl className="mini"><div><dt>Actual model-read event IDs</dt><dd>{idsText(claims?.read_event_ids)}</dd></div><div><dt>Selected / evidence IDs</dt><dd>{idsText(claims?.selected_event_ids ?? claims?.evidence_event_ids)}</dd></div></dl></section>
          <div className="grid2">
            {["observations", "inferences"].map((key) => (<section className="subpanel" key={key}><h4>{key === "observations" ? "Model observations (claims with citations)" : "Model inferences (claims)"}</h4>{arr(claims?.[key]).length ? <ul className="statements">{arr(claims?.[key]).map((s, i) => isRec(s) ? <li key={i}><p>{str(s.text) ?? "Untitled statement"}</p><span className="citations">Cited events: {idsText(s.evidence_event_ids)}</span></li> : null)}</ul> : <p className="muted">None reported.</p>}</section>))}
          </div>
          <section className="subpanel amber"><h4>Uncertainties (model claims)</h4>{arr(claims?.uncertainties).length ? <ul>{arr(claims?.uncertainties).map((u, i) => <li key={i}>{typeof u === "string" ? u : boundedJson(u)}</li>)}</ul> : <p className="muted">None reported.</p>}</section>
          <section className="subpanel"><h4>Initial snapshot &amp; preview checks</h4>
            {!usablePreview ? <p className="warn-text">Initial preview unavailable{previewUnavailable ? `: ${str(preview?.reason) ?? "unavailable"}` : "."} It is not treated as a usable preview.</p> : (<div className="stack">
              <dl className="mini grid-info"><div><dt>Before hash / fingerprint</dt><dd className="mono cases-wrap">{str(before?.snapshot_hash) ?? str(before?.fingerprint) ?? "unavailable"}</dd></div><div><dt>Preview snapshot match (at preview time)</dt><dd>{bool(usablePreview.snapshot_match) === null ? "unknown" : usablePreview.snapshot_match ? "Yes" : "No"}</dd></div><div><dt>Simulation pass</dt><dd>{bool(usablePreview.simulation_pass) === null ? "unknown" : usablePreview.simulation_pass ? "Yes" : "No"}</dd></div><div><dt>Reverse IDs</dt><dd>{previewProposal ? idsText(previewProposal.reverse_event_ids) : "unavailable — proposal missing or malformed"}</dd></div><div><dt>Preserved restocks</dt><dd>{idsText(usablePreview.preserved_restock_ids)}</dd></div></dl>
              <div className="grid2"><div><h4>Simulation checks</h4>{checksOf(usablePreview.simulation_checks).length ? <ul className="checks">{checksOf(usablePreview.simulation_checks).map((c) => <li key={c.name} className={c.ok ? "ok" : "bad"}><span aria-hidden="true">{c.ok ? "✓" : "✕"}</span><span><strong>{c.name}</strong>{c.detail ? <small>{c.detail}</small> : null}</span></li>)}</ul> : <p className="muted">No checks reported.</p>}</div><div><h4>Invariants</h4>{checksOf(usablePreview.invariants).length ? <ul className="checks">{checksOf(usablePreview.invariants).map((c) => <li key={c.name} className={c.ok ? "ok" : "bad"}><span aria-hidden="true">{c.ok ? "✓" : "✕"}</span><span><strong>{c.name}</strong>{c.detail ? <small>{c.detail}</small> : null}</span></li>)}</ul> : <p className="muted">No invariants reported.</p>}</div></div>
            </div>)}
          </section>
          <section className="subpanel"><h4>Rust evidence</h4>
            <p className="label-note">Initial preview and approval/apply audit Rust evidence are PRE-MUTATION. Report refresh does not invoke Rust. Final preservation below is current Python verification.</p>
            {rust ? <dl className="mini grid-info"><div><dt>Status</dt><dd>{str(rust.status) ?? "unavailable"}</dd></div><div><dt>Verifier version</dt><dd className="mono">{str(rust.verifier_version) ?? "unavailable"}</dd></div><div><dt>Ledger actual (canonical)</dt><dd>{numText(rust.ledger_actual)}</dd></div><div><dt>Expected stock</dt><dd>{numText(rust.expected_stock)}</dd></div><div><dt>Outstanding credit</dt><dd>{numText(rust.outstanding_credit)}</dd></div><div><dt>Proposal matches</dt><dd>{bool(rust.proposal_matches) === null ? "unknown" : rust.proposal_matches ? "Yes" : "No"}</dd></div><div><dt>Snapshot hash</dt><dd className="mono cases-wrap">{str(rust.snapshot_hash) ?? "unavailable"}</dd></div><div><dt>Error code</dt><dd>{str(rust.error_code) ?? "none reported"}</dd></div></dl> : <p className="warn-text">Rust verification unavailable in the saved initial preview.</p>}
            {rust && checksOf(rust.checks).length > 0 && <ul className="checks">{checksOf(rust.checks).map((c) => <li key={c.name} className={c.ok ? "ok" : "bad"}><span aria-hidden="true">{c.ok ? "✓" : "✕"}</span><span><strong>{c.name}</strong>{c.detail ? <small>{c.detail}</small> : null}</span></li>)}</ul>}
          </section>
          <div className="grid2">
            <section className="subpanel"><h4>Approval / apply audit facts</h4>{approvals.length || audit.length ? <div className="stack">{approvals.length > 0 && <div><strong>Approvals</strong><pre className="cases-pre">{boundedJson(approvals)}</pre></div>}{audit.length > 0 && <div><strong>Audit entries</strong><pre className="cases-pre">{boundedJson(audit)}</pre></div>}</div> : <p className="muted">No approval/apply audit entries reported.</p>}</section>
            <section className="subpanel"><h4>Final current preservation verification (Python)</h4>
              {!fv || fvUnavailable ? <p className="warn-text">Final verification unavailable / not run{fv && str(fv.reason) ? `: ${str(fv.reason)}` : "."}</p> : (<div className="stack"><dl className="mini"><div><dt>Final stock</dt><dd>{numText(fv.final_stock)}</dd></div><div><dt>Pass</dt><dd>{fv.pass === true ? "Yes" : "No"}</dd></div><div><dt>Preservation verified</dt><dd>{fv.preservation_verified === true ? "Yes" : "No"}</dd></div></dl>{fvChecks.length ? <ul className="checks">{fvChecks.map((c) => <li key={c.name} className={c.ok ? "ok" : "bad"}><span aria-hidden="true">{c.ok ? "✓" : "✕"}</span><span><strong>{c.name}</strong>{c.detail ? <small>{c.detail}</small> : null}</span></li>)}</ul> : <p className="warn-text">No verification checks reported — not verified.</p>}</div>)}
            </section>
          </div>
          <details className="cases-details"><summary>Raw bounded before evidence, patch and current data (text only)</summary>
            {patchText ? <><h4>Original patch</h4><pre className="cases-pre">{patchText}</pre></> : <p className="muted">Patch unavailable.</p>}
            <h4>Before section</h4><pre className="cases-pre">{boundedJson(before)}</pre>
            <h4>Current section</h4><pre className="cases-pre">{boundedJson(currentSection)}</pre>
          </details>
          </>)}
        </div>
      )}
    </section>
  );
}
