import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, apiPost, apiGet } from "./api";
import CaseReports from "./CaseReports";
import { isActiveVerified, isRustApprovalReady, isValidPassingChecks } from "./caseState";
import type {
  ApplyResponse, ApprovalResponse, AuthMeResponse, CitedStatement, EvidenceEvent, EvidenceResponse,
  IncidentResponse, InvestigationResult, PatchResponse, PreviewResponse, ResetResponse, VerifyResponse,
} from "./types";

type Stage = 0 | 1 | 2 | 3;
const STAGES = ["Investigate", "Preview", "Approve & apply", "Verify"] as const;
const DEFAULT_INCIDENT = "INC-001";

function Icon({ d }: { d: string }) {
  return (<svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d={d} strokeLinecap="round" strokeLinejoin="round" /></svg>);
}
function newKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return `afterfix-${crypto.randomUUID()}`;
  return `afterfix-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}
function formatCheckName(name: string): string {
  const readable = name.replace(/_/g, " ").replace(/\s+/g, " ").trim();
  return readable ? readable.charAt(0).toUpperCase() + readable.slice(1) : name;
}
function formatTs(ts: number): string { return new Date(ts * 1000).toLocaleString(); }
function isApplySuccess(result: ApplyResponse | null): boolean {
  return !!result && (result.status === "applied" || result.status === "already_applied");
}
function isVerified(result: VerifyResponse | null): boolean { return isActiveVerified(result); }
function unrelatedLabel(item: unknown): string {
  if (typeof item === "string") return item;
  if (typeof item === "number") return String(item);
  if (item && typeof item === "object") {
    const record = item as Record<string, unknown>;
    const candidate = record.product_id ?? record.id ?? record.name;
    if (typeof candidate === "string" || typeof candidate === "number") return String(candidate);
    return JSON.stringify(item);
  }
  return String(item);
}
function CheckList({ checks }: { checks: { name: string; ok: boolean; detail?: string }[] }) {
  if (!checks.length) return <p className="muted">No checks were returned.</p>;
  return (<ul className="checks">{checks.map((c) => (
    <li key={c.name} className={c.ok ? "ok" : "bad"}>
      <span aria-hidden="true">{c.ok ? "✓" : "✕"}</span>
      <span><strong>{formatCheckName(c.name)}</strong>{c.detail ? <small>{c.detail}</small> : null}</span>
    </li>))}</ul>);
}
function numText(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "unknown / not reported";
}
function investigationReasonText(reason: string | null | undefined): string {
  switch (reason) {
    case "model_client_error":
    case "client_unavailable":
      return "The model service could not complete this investigation, so no repair was proposed. Nothing was retried automatically; review the evidence or explicitly run a fresh investigation.";
    case "model_output_limit":
      return "The model response reached its length limit, so that result was not used and no partial repair was proposed. Review the evidence or explicitly run a fresh investigation.";
    case "model_needs_review":
      return "The available evidence was not sufficient to support a repair, so manual review is required and no repair was proposed.";
    case "proposal_created":
      return "A draft proposal was created for review. This does not claim final verification; review the preview before any approval or apply.";
    case "model_refusal":
      return "The model did not complete this investigation, so no repair was proposed and manual review is required.";
    case "stale_state":
      return "The recorded data changed during the investigation, so no repair was proposed. Run a fresh investigation against the current data.";
    case "deterministic_validation_abstained":
    case "snapshot_validation_error":
    case "live_proposal_validation_failed":
    case "live_proposal_error":
      return "Validation did not support a repair, so no applicable repair was proposed and manual review is required.";
    default:
      return "This investigation requires review, so no applicable repair was proposed. Review the recorded evidence; nothing was retried automatically.";
  }
}

export default function App({ actor }: { actor: AuthMeResponse }) {
  const isViewer = actor.role === "viewer";
  const canReset = actor.role === "operator" && actor.can_reset;
  const [incidentId, setIncidentId] = useState(DEFAULT_INCIDENT);
  const [incident, setIncident] = useState<IncidentResponse | null>(null);
  const [evidence, setEvidence] = useState<EvidenceResponse | null>(null);
  const [patch, setPatch] = useState<string>("");
  const [notSeeded, setNotSeeded] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [stage, setStage] = useState<Stage>(0);
  const [investigation, setInvestigation] = useState<InvestigationResult | null>(null);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [approval, setApproval] = useState<ApprovalResponse | null>(null);
  const [applyResult, setApplyResult] = useState<ApplyResponse | null>(null);
  const [verifyResult, setVerifyResult] = useState<VerifyResponse | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState<EvidenceEvent | null>(null);
  const [dialogMode, setDialogMode] = useState<"event" | "patch" | null>(null);
  const [caseRefresh, setCaseRefresh] = useState(0);
  const [caseClear, setCaseClear] = useState(0);
  const [focusCaseId, setFocusCaseId] = useState<string | null>(null);
  const applyKeyRef = useRef<string | null>(null);
  const mutationLockRef = useRef(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const lastFocusRef = useRef<HTMLElement | null>(null);

  const busyAny = busy !== null;
  const resetting = busy === "reset";
  const proposalId = investigation?.proposal_id ?? preview?.proposal.proposal_id ?? null;
  const refreshCases = useCallback(() => setCaseRefresh((n) => n + 1), []);

  const acquireMutation = useCallback((action: string): boolean => {
    if (mutationLockRef.current) return false;
    mutationLockRef.current = true; setBusy(action); return true;
  }, []);
  const releaseMutation = useCallback(() => { mutationLockRef.current = false; setBusy(null); }, []);
  const invalidateStale = useCallback(() => {
    setInvestigation(null); setPreview(null); setApproval(null); setApplyResult(null); setVerifyResult(null);
    setReviewed(false); applyKeyRef.current = null; setStage(0);
  }, []);
  const clearDownstream = useCallback(() => { invalidateStale(); }, [invalidateStale]);

  const loadIncident = useCallback(async (id: string) => {
    const [inc, ev, pa] = await Promise.all([
      apiGet<IncidentResponse>(`/api/incidents/${encodeURIComponent(id)}`),
      apiGet<EvidenceResponse>(`/api/incidents/${encodeURIComponent(id)}/evidence`),
      apiGet<PatchResponse>(`/api/incidents/${encodeURIComponent(id)}/patch`),
    ]);
    setIncident(inc); setEvidence(ev); setPatch(pa.patch); setNotSeeded(false);
  }, []);
  const refreshIncident = useCallback(async (id: string) => {
    const [inc, ev] = await Promise.all([
      apiGet<IncidentResponse>(`/api/incidents/${encodeURIComponent(id)}`),
      apiGet<EvidenceResponse>(`/api/incidents/${encodeURIComponent(id)}/evidence`),
    ]);
    setIncident(inc); setEvidence(ev);
  }, []);
  const refreshAfterStale = useCallback(async (): Promise<boolean> => {
    try { await refreshIncident(incidentId); refreshCases(); return true; } catch { return false; }
  }, [refreshIncident, incidentId, refreshCases]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try { await loadIncident(DEFAULT_INCIDENT); }
      catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) setNotSeeded(true);
        else setError(e instanceof Error ? e.message : "Could not load the incident.");
      } finally { if (!cancelled) setInitialLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [loadIncident]);

  useEffect(() => {
    const dlg = dialogRef.current;
    if (!dlg) return;
    if (dialogMode && !dlg.open) { lastFocusRef.current = document.activeElement as HTMLElement; dlg.showModal(); }
    if (!dialogMode && dlg.open) dlg.close();
  }, [dialogMode, selectedEvent]);

  const restoreFocus = () => { lastFocusRef.current?.focus(); };
  const closeDialog = () => { setDialogMode(null); setSelectedEvent(null); restoreFocus(); };
  const handleDialogClose = () => { setDialogMode(null); setSelectedEvent(null); restoreFocus(); };
  const openEvent = (ev: EvidenceEvent) => { lastFocusRef.current = document.activeElement as HTMLElement; setSelectedEvent(ev); setDialogMode("event"); };

  const eventsById = useMemo(() => new Map((evidence?.events ?? []).map((e) => [e.id, e])), [evidence]);
  const sortedEvents = useMemo(() => [...(evidence?.events ?? [])].sort((a, b) => (a.ts - b.ts) || (a.id - b.id)), [evidence]);
  const reverseIds = useMemo(() => new Set(preview?.proposal.reverse_event_ids ?? []), [preview]);
  const preservedIds = useMemo(() => new Set(preview?.preserved_restock_ids ?? []), [preview]);

  const rust = preview?.rust_verification ?? null;
  const rustValid = isRustApprovalReady(rust, preview?.proposal.snapshot_hash);
  const isValidPreview = !!preview && preview.snapshot_match === true && preview.simulation_pass === true && isValidPassingChecks(preview.simulation_checks) && isValidPassingChecks(preview.invariants) && rustValid;
  const applySucceeded = isApplySuccess(applyResult);
  const verified = isVerified(verifyResult);
  const canApprove = !isViewer && isValidPreview && !!preview && preview.proposal.status === "proposed" && reviewed && !approval && !applyResult;
  const furthest: Stage = applySucceeded ? 3 : isValidPreview ? 2 : preview ? 1 : 0;

  async function runReset() {
    if (!canReset) { setError(isViewer ? "Viewers have read-only access and cannot load or reset demo data." : "Reset is not permitted for this account."); return; }
    if (!acquireMutation("reset")) return;
    // Reset-start invalidation happens through the resetting prop: CaseReports
    // bumps its generation, clears visible history/report/download state and
    // suspends history loads before this POST settles.
    setError(null); setNotice(null); setFocusCaseId(null); clearDownstream();
    try {
      const r = await apiPost<ResetResponse>("/api/admin/reset", {});
      setIncidentId(r.incident_id); setSelectedEvent(null); setDialogMode(null);
      await loadIncident(r.incident_id);
      // After the actual outcome is known, signal a fresh history load/clear.
      setCaseClear((n) => n + 1);
      setNotice(`Synthetic demo data loaded for ${r.incident_id}. Saved cases were cleared by the reset.`);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load demo data."); }
    finally { releaseMutation(); }
  }

  async function runInvestigate() {
    if (isViewer) { setError("Viewers have read-only access and cannot run an investigation."); return; }
    if (!acquireMutation("investigate")) return;
    setError(null); setNotice(null);
    setInvestigation(null); setPreview(null); setApproval(null); setApplyResult(null); setVerifyResult(null); setReviewed(false); applyKeyRef.current = null; setStage(0);
    try {
      if (!incident) { setError("No incident is loaded."); return; }
      const result = await apiPost<InvestigationResult>(`/api/incidents/${encodeURIComponent(incidentId)}/investigate`, {});
      setInvestigation(result);
      if (result.case_recording_status === "recorded" && result.case_id) {
        setFocusCaseId(result.case_id); refreshCases();
      } else if (result.case_recording_status === "unavailable" || result.case_id === null) {
        setFocusCaseId(null);
        setNotice("Case recording unavailable: no durable case ID or report was created. This live investigation may continue, but there is no authoritative durable export for it. It was not rerun automatically.");
      }
      if (result.status === "needs_review" || !result.proposal_id) {
        setStage(0);
        if (result.case_recording_status === "recorded") setNotice("The investigation stopped for review. No repair draft was created.");
        return;
      }
      const pv = await apiGet<PreviewResponse>(`/api/proposals/${encodeURIComponent(result.proposal_id)}/preview`);
      setPreview(pv); setStage(1);
    } catch (e) { setError(e instanceof Error ? e.message : "Investigation could not be completed."); }
    finally { releaseMutation(); }
  }

  async function runApprove() {
    if (isViewer) { setError("Viewers have read-only access and cannot approve a repair."); return; }
    if (!acquireMutation("approve")) return;
    setError(null);
    try {
      if (!preview || preview.proposal.status !== "proposed" || approval || applyResult) return;
      if (!reviewed || !isValidPreview) { setError("Review a valid preview with passing checks and verified Rust evidence before approving."); return; }
      const currentPreview = preview;
      const ap = await apiPost<ApprovalResponse>(`/api/proposals/${encodeURIComponent(currentPreview.proposal.proposal_id)}/approve`, { snapshot_hash: currentPreview.proposal.snapshot_hash });
      if (ap.proposal_id !== currentPreview.proposal.proposal_id || ap.snapshot_hash !== currentPreview.proposal.snapshot_hash) {
        setError("Approval did not match this preview. Run a fresh investigation."); invalidateStale(); return;
      }
      setApproval(ap);
      if (!applyKeyRef.current) applyKeyRef.current = newKey();
      setStage(2); refreshCases();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) { invalidateStale(); const refreshed = await refreshAfterStale(); setError(refreshed ? "The data changed since preview. Run a fresh investigation to create a new preview." : "The data changed since preview. Run a fresh investigation to create a new preview. The displayed incident data could not be refreshed and may be out of date."); }
      else setError(e instanceof Error ? e.message : "Approval could not be recorded.");
    } finally { releaseMutation(); }
  }

  async function runApply() {
    if (isViewer) { setError("Viewers have read-only access and cannot apply a repair."); return; }
    if (!acquireMutation("apply")) return;
    setError(null);
    try {
      if (!preview || !approval || applyResult) return;
      if (approval.proposal_id !== preview.proposal.proposal_id || approval.snapshot_hash !== preview.proposal.snapshot_hash) {
        setError("Approval does not match this preview. Run a fresh investigation."); invalidateStale(); return;
      }
      if (!applyKeyRef.current) applyKeyRef.current = newKey();
      const key = applyKeyRef.current;
      const r = await apiPost<ApplyResponse>("/api/apply", { proposal_id: preview.proposal.proposal_id, approval_id: approval.approval_id, idempotency_key: key });
      if (!isApplySuccess(r)) { setError(`Apply returned an unrecognised status (${r.status || "unknown"}). The repair has not been confirmed.`); return; }
      setApplyResult(r); setStage(3); refreshCases();
      await refreshIncident(incidentId);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) { invalidateStale(); const refreshed = await refreshAfterStale(); setError(refreshed ? "The data changed since preview. Run a fresh investigation to create a new preview." : "The data changed since preview. Run a fresh investigation to create a new preview. The displayed incident data could not be refreshed and may be out of date."); }
      else setError(e instanceof Error ? e.message : "The repair could not be applied. You can retry with the same idempotency key.");
    } finally { releaseMutation(); }
  }

  async function runVerify() {
    if (!acquireMutation("verify")) return;
    setError(null);
    try {
      if (!incident || !applySucceeded || !proposalId) { setError("Apply a repair before verifying."); return; }
      if (preview && proposalId !== preview.proposal.proposal_id) { setError("Verification does not match this preview."); return; }
      if (approval && approval.proposal_id !== proposalId) { setError("Verification does not match the approval."); return; }
      const v = await apiPost<VerifyResponse>("/api/verify", { product_id: incident.incident.product_id, proposal_id: proposalId });
      setVerifyResult(v); refreshCases();
      await refreshIncident(incidentId);
    } catch (e) { setError(e instanceof Error ? e.message : "Verification could not be completed."); }
    finally { releaseMutation(); }
  }

  const renderCitations = (ids: number[]) => ids.length ? (
    <span className="citations">Evidence: {ids.map((id) => {
      const ev = eventsById.get(id);
      return (<button key={id} type="button" className="cite" onClick={() => ev && openEvent(ev)} disabled={!ev || busyAny}>#{id}</button>);
    })}</span>
  ) : null;
  const statementBlock = (title: string, items: CitedStatement[], kind: string) => (
    <section className="subpanel"><h4>{title}</h4>{items.length ? <ul className="statements">{items.map((s, i) => (
      <li key={i}><p>{s.text}</p>{renderCitations(s.evidence_event_ids)}</li>))}</ul>
      : <p className="muted">None returned.</p>}<p className="label-note">{kind}</p></section>
  );

  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand"><span className="brand-mark" aria-hidden="true">A</span><div><strong>AfterFix</strong><span>Recovery workspace</span></div></div>
        <p className="demo-tag">Synthetic inventory / Local demo</p>
        <div className="rail-card">
          <span className="eyebrow">Current incident</span>
          <strong className="mono">{incident?.incident.id ?? incidentId}</strong>
          {incident ? (<dl><div><dt>DB incident status</dt><dd>{incident.incident.status}</dd></div><div><dt>Product</dt><dd>{incident.product.name} <span className="mono">({incident.product.product_id})</span></dd></div><div><dt>Current recorded stock</dt><dd>{incident.product.stock}</dd></div></dl>) : <p className="muted">No incident loaded.</p>}
        </div>
        <button type="button" className="btn btn-ghost" onClick={runReset} disabled={busyAny || !canReset}>{busy === "reset" ? "Loading demo…" : incident ? "Reset demo" : "Load demo"}</button>
        <p className="rail-note">{isViewer ? "Read-only viewer: demo data must be loaded by an operator." : canReset ? "Reset loads synthetic demo data only and clears saved cases." : "Reset is not permitted for this account."}</p>
        {patch && <button type="button" className="btn btn-ghost" onClick={() => setDialogMode("patch")} disabled={busyAny}><Icon d="M8 6l-6 6 6 6M16 6l6 6-6 6" /> Code patch</button>}
      </aside>

      <main className="workspace">
        <header className="hero"><p className="eyebrow">Evidence-driven data recovery</p><h1>Recover the data left behind.</h1><p className="lede">AfterFix examines the recorded evidence, previews a bounded repair, and only applies it after your explicit approval.</p></header>
        {isViewer && <div className="banner warn" role="status"><strong>Read-only viewer.</strong> You can inspect the incident, evidence, patch and saved case reports. Investigation, reset, approval and apply are disabled.</div>}
        <div aria-live="polite">{initialLoading && <div className="banner info" role="status">Loading incident and evidence…</div>}</div>
        <div aria-live="assertive">{error && <div className="banner error" role="alert">{error}</div>}</div>
        <div aria-live="polite">{notice && <div className="banner info" role="status">{notice}</div>}</div>

        {notSeeded && !incident ? (
          <section className="panel empty"><h2>No demo incident is loaded yet</h2>{isViewer ? <p>An operator must load the synthetic inventory demo before you can inspect it.</p> : (<><p>Load the synthetic inventory demo to begin.</p><button type="button" className="btn btn-primary" onClick={runReset} disabled={busyAny || !canReset}>{busy === "reset" ? "Loading…" : "Load demo"}</button></>)}</section>
        ) : incident && evidence ? (<>
          <nav className="stages" aria-label="Recovery stages">{STAGES.map((label, i) => {
            const idx = i as Stage; const done = idx < furthest || (idx === 3 && verified);
            return (<button key={label} type="button" className={`stage ${stage === idx ? "active" : ""} ${done ? "done" : ""}`} aria-current={stage === idx ? "step" : undefined} disabled={busyAny || idx > furthest} onClick={() => setStage(idx)}><span className="stage-num">{done ? "✓" : i + 1}</span>{label}</button>);
          })}</nav>

          <section className="panel incident-panel">
            <div><span className="eyebrow">Incident</span><h2>{incident.product.name} stock discrepancy</h2><p>{incident.incident.description}</p><p className="muted">Database incident status (separate from recovery state): {incident.incident.status}</p></div>
            <dl className="stats"><div><dt>Product ID</dt><dd className="mono">{incident.product.product_id}</dd></div><div><dt>Current recorded stock</dt><dd>{incident.product.stock}</dd></div><div><dt>Events</dt><dd>{evidence.events.length}</dd></div><div><dt>Snapshot</dt><dd className="mono short" title={evidence.snapshot_hash}>{evidence.snapshot_hash.slice(0, 12)}…</dd></div></dl>
          </section>

          {stage === 0 && (<section className="panel"><div className="panel-head"><div><span className="eyebrow">Stage 1</span><h2>Investigate the evidence</h2></div>
            <button type="button" className="btn btn-primary" onClick={runInvestigate} disabled={busyAny || isViewer}>{busy === "investigate" ? "Investigating…" : investigation ? "Investigate again (uses model requests)" : "Run investigation (uses model requests)"}</button></div>
            {busy === "investigate" && <p className="muted" role="status">Investigating the patch and recorded events…</p>}
            {isViewer && <p className="muted">Viewers cannot run an investigation.</p>}
            {!investigation && busy !== "investigate" && !isViewer && <p className="muted">Starting a fresh investigation is explicit and uses model requests. Nothing is approved or applied automatically.</p>}
            {investigation && (<div className="stack">
              {investigation.case_recording_status === "recorded" && investigation.case_id && <p className="muted">Durable case recorded: <span className="mono">{investigation.case_id}</span> (see Saved cases — read-only history).</p>}
              {investigation.case_recording_status === "unavailable" && <div className="banner warn" role="status"><strong>Case recording unavailable.</strong> No durable case ID, version or report exists for this live investigation; it may continue, but authoritative durable export is disabled for it.</div>}
              {investigation.status === "needs_review" ? <div className="banner warn" role="status"><strong>Needs review — no repair proposed.</strong><br />{investigationReasonText(investigation.reason)}</div> : <div className="banner ok" role="status">A draft proposal was returned. Review the preview before any approval.</div>}
              {investigation.hypothesis && <section className="subpanel"><h4>Model hypothesis</h4><p>{investigation.hypothesis}</p><p className="label-note">Model explanation — not a deterministic finding.</p></section>}
              {investigation.status !== "needs_review" && <p>{investigationReasonText(investigation.reason)}</p>}
              <div className="grid2">{statementBlock("Observations", investigation.observations, "Directly tied to cited event records.")}{statementBlock("Model inferences", investigation.inferences, "Model claims — check them against the cited evidence.")}</div>
              <section className="subpanel amber"><h4>Uncertainties</h4>{investigation.uncertainties.length ? <ul>{investigation.uncertainties.map((u, i) => <li key={i}>{u}</li>)}</ul> : <p className="muted">None returned.</p>}</section>
              <div className="grid2"><section className="subpanel"><h4>Investigation trace</h4>{investigation.trace.length ? <ul className="trace">{investigation.trace.map((t, i) => <li key={i}><span className="mono">{t.tool}</span><span>{t.count} record{t.count === 1 ? "" : "s"}{t.event_ids.length ? ` · IDs ${t.event_ids.join(", ")}` : ""}</span></li>)}</ul> : <p className="muted">No trace returned.</p>}</section>
              <section className="subpanel"><h4>Model &amp; usage</h4><dl className="mini"><div><dt>Model</dt><dd className="mono">{investigation.model ?? "Not reported"}</dd></div><div><dt>Requests</dt><dd>{numText(investigation.usage?.requests)}</dd></div><div><dt>Input tokens</dt><dd>{numText(investigation.usage?.input_tokens)}</dd></div><div><dt>Output tokens</dt><dd>{numText(investigation.usage?.output_tokens)}</dd></div><div><dt>Total tokens</dt><dd>{numText(investigation.usage?.total_tokens)}</dd></div><div><dt>Estimated cost</dt><dd>{typeof investigation.usage?.estimated_cost_usd === "number" && Number.isFinite(investigation.usage.estimated_cost_usd) ? `$${investigation.usage.estimated_cost_usd.toFixed(5)}` : "unknown / not reported"}</dd></div></dl></section></div>
              {preview && <button type="button" className="btn btn-primary" onClick={() => setStage(1)} disabled={busyAny}>Continue to preview</button>}
            </div>)}
          </section>)}

          {stage === 1 && (<section className="panel"><div className="panel-head"><div><span className="eyebrow">Stage 2</span><h2>Preview the proposed repair</h2></div>{preview && <span className={`pill ${isValidPreview ? "pill-ok" : "pill-bad"}`}>{isValidPreview ? "Deterministic and Rust checks passed" : "Checks need attention"}</span>}</div>
            {!preview ? <p className="muted">No proposal exists yet. {isViewer ? "An operator must run an investigation first." : "Run an investigation first."}</p> : (<div className="stack">
              <p className="label-note">{investigation?.explanations_label ?? "Model explanation is labelled separately from deterministic checks."}</p>
              {investigation?.hypothesis && <section className="subpanel"><h4>Model explanation</h4><p>{investigation.hypothesis}</p>{investigation.evidence_event_ids.length > 0 && renderCitations(investigation.evidence_event_ids)}</section>}
              <div className="diff-cards"><div className="diff-card"><span>Before (original)</span><strong>{preview.before_stock}</strong><small>original recorded stock in this preview</small></div><div className="diff-arrow" aria-hidden="true">→</div><div className="diff-card after"><span>After (original proposed)</span><strong>{preview.after_stock}</strong><small>stock delta {preview.diff.stock_delta >= 0 ? "+" : ""}{preview.diff.stock_delta} · credit {preview.credit_qty}</small></div></div>
              <p className="muted">Current recorded stock is shown separately in the incident summary ({incident.product.stock}); this original preview does not change after apply.</p>
              <dl className="mini grid-info"><div><dt>Proposal</dt><dd className="mono">{preview.proposal.proposal_id}</dd></div><div><dt>Status</dt><dd>{preview.proposal.status}</dd></div><div><dt>Events to reverse</dt><dd>{preview.proposal.reverse_event_ids.length ? preview.proposal.reverse_event_ids.map((id) => `#${id}`).join(", ") : "None"}</dd></div><div><dt>Preserved restocks</dt><dd>{preview.preserved_restock_ids.length ? preview.preserved_restock_ids.map((id) => `#${id}`).join(", ") : "None"}</dd></div><div><dt>Events added by repair</dt><dd>{preview.diff.events_added}</dd></div><div><dt>Restocks untouched</dt><dd>{preview.diff.restocks_untouched.length} restock{preview.diff.restocks_untouched.length === 1 ? "" : "s"}{preview.diff.restocks_untouched.length ? ` — IDs ${preview.diff.restocks_untouched.map((id) => `#${id}`).join(", ")}` : ""}</dd></div><div><dt>Snapshot matches current data</dt><dd>{preview.snapshot_match ? "Yes" : "No — stale preview"}</dd></div><div><dt>Other products in baseline</dt><dd>{preview.unrelated_products.length ? `${preview.unrelated_products.length} baseline product${preview.unrelated_products.length === 1 ? "" : "s"}: ${preview.unrelated_products.map(unrelatedLabel).join(", ")}` : "None"} (baseline only — not affected by this repair)</dd></div></dl>
              <section className="subpanel"><h4>Rust verification (preview, pre-mutation)</h4>{rust ? <dl className="mini grid-info"><div><dt>Status</dt><dd>{typeof rust.status === "string" ? rust.status : "unavailable"}</dd></div><div><dt>Verifier version</dt><dd className="mono">{rust.verifier_version ?? "unavailable"}</dd></div><div><dt>Ledger actual</dt><dd>{numText(rust.ledger_actual)}</dd></div><div><dt>Expected stock</dt><dd>{numText(rust.expected_stock)}</dd></div><div><dt>Outstanding credit</dt><dd>{numText(rust.outstanding_credit)}</dd></div><div><dt>Snapshot hash</dt><dd className="mono">{rust.snapshot_hash ?? "unavailable"}</dd></div></dl> : <p className="warn-text">Rust verification unavailable — approval is disabled.</p>}{rust && !rustValid && <p className="warn-text">Rust verification is not an enabled, verified, snapshot-matching result with all passing checks — approval is disabled.</p>}</section>
              <div className="grid2"><section className="subpanel"><h4>Simulation (deterministic)</h4><CheckList checks={preview.simulation_checks} /></section><section className="subpanel"><h4>Invariants (deterministic)</h4><CheckList checks={preview.invariants} /></section></div>
              {!preview.snapshot_match && <div className="banner warn">This preview is stale. Approval is disabled; run a fresh investigation.</div>}
              {isValidPreview && <button type="button" className="btn btn-primary" onClick={() => setStage(2)} disabled={busyAny}>Continue to approve &amp; apply</button>}
            </div>)}
          </section>)}

          {stage === 2 && (<section className="panel"><span className="eyebrow">Stage 3</span><h2>Approve &amp; apply</h2>
            {!preview ? <p className="muted">No proposal is available to approve.</p> : (<div className="stack">
              <p>Approval is for proposal <span className="mono">{preview.proposal.proposal_id}</span> and snapshot <span className="mono">{preview.proposal.snapshot_hash.slice(0, 16)}…</span>. Apply is a separate action.</p>
              {isViewer && <p className="warn-text">Viewers cannot approve or apply a repair.</p>}
              <label className="review-check"><input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} disabled={busyAny || !!approval || isViewer} /> I reviewed the preview, the events to reverse, and the preserved later activity.</label>
              <div className="actions"><button type="button" className="btn btn-primary" onClick={runApprove} disabled={busyAny || !canApprove}>{busy === "approve" ? "Approving…" : approval ? "Approved" : "Approve this repair"}</button>
              <button type="button" className="btn btn-danger" onClick={runApply} disabled={busyAny || isViewer || !approval || !!applyResult}>{busy === "apply" ? "Applying…" : "Apply approved repair"}</button></div>
              {!preview.snapshot_match && <p className="warn-text">Approval disabled: the snapshot is stale.</p>}
              {!preview.simulation_pass && <p className="warn-text">Approval disabled: simulation did not pass.</p>}
              {preview.snapshot_match && preview.simulation_pass && !isValidPassingChecks(preview.invariants) && <p className="warn-text">Approval disabled: invariants are missing, malformed or did not all pass.</p>}
              {!rustValid && <p className="warn-text">Approval disabled: valid Rust verification (enabled, verified, known version, snapshot match, proposal match, all checks passing) is required.</p>}
              {approval && !applyResult && <p className="muted">Approval <span className="mono">{approval.approval_id}</span> recorded. Retrying apply reuses the same idempotency key.</p>}
              {applySucceeded && applyResult && <div className="banner ok" role="status">Repair {applyResult.status === "already_applied" ? "was already applied — it was not applied twice" : "applied"}{typeof applyResult.before_stock === "number" && typeof applyResult.after_stock === "number" ? `: stock ${applyResult.before_stock} → ${applyResult.after_stock}` : ""}. This is applied, not yet verified. Continue to verification.</div>}
              {applySucceeded && <button type="button" className="btn btn-primary" onClick={() => setStage(3)} disabled={busyAny}>Continue to verify</button>}
            </div>)}
          </section>)}

          {stage === 3 && (<section className="panel"><div className="panel-head"><div><span className="eyebrow">Stage 4</span><h2>Verify the recovery</h2></div>{applySucceeded && proposalId && <button type="button" className="btn btn-primary" onClick={runVerify} disabled={busyAny}>{busy === "verify" ? "Verifying…" : verifyResult ? "Verify again" : "Run verification"}</button>}</div>
            {!applySucceeded && <p className="muted">Verification runs after the approved repair has been applied.</p>}
            {applySucceeded && !verifyResult && <p className="muted">Repair applied but unverified. Run verification.</p>}
            {verifyResult && (<div className="stack">{verified ? <div className="banner ok" role="status"><strong>Verified.</strong> Final recorded stock is {verifyResult.final_stock}, and preservation of valid later activity was verified.</div> : <div className="banner warn" role="alert"><strong>Applied but unverified / needs review.</strong> Final recorded stock is {verifyResult.final_stock}; exact passing flags and all-valid, non-empty preservation checks are required for “verified”.</div>}
              <dl className="mini grid-info"><div><dt>Final stock</dt><dd>{verifyResult.final_stock}</dd></div><div><dt>Overall pass</dt><dd>{verifyResult.pass === true ? "Yes" : "No"}</dd></div><div><dt>Preservation verified</dt><dd>{verifyResult.preservation_verified === true ? "Yes" : "No"}</dd></div></dl>
              <CheckList checks={verifyResult.checks} />
              <p className="muted">For the authoritative full export, use “Download full server report” in Saved cases below. There is no separate session-only export.</p>
              <div className="actions"><button type="button" className="btn btn-ghost" onClick={() => setStage(0)} disabled={busyAny}>Back to investigation</button></div>
              </div>)}
          </section>)}

          <CaseReports incidentId={incident.incident.id} refreshSignal={caseRefresh} clearSignal={caseClear} focusCaseId={focusCaseId} resetting={resetting} />

          <section className="panel"><div className="panel-head"><div><span className="eyebrow">Evidence timeline</span><h2>Recorded events for {incident.product.name}</h2></div><span className="muted">{sortedEvents.length} events</span></div>
            <p className="muted">{evidence.note}</p>
            {preview && <p className="legend"><span className="sw reverse" /> {applySucceeded ? "Reversed by this repair" : "To be reversed"} <span className="sw preserve" /> Preserved restock</p>}
            <ol className="timeline">{sortedEvents.map((ev) => {
              const cls = reverseIds.has(ev.id) ? "reverse" : preservedIds.has(ev.id) ? "preserve" : "";
              return (<li key={ev.id} id={`event-${ev.id}`} className={`${cls} ${selectedEvent?.id === ev.id ? "selected" : ""}`}>
                <div className="event-main"><span className="mono event-id">#{ev.id}</span><div><strong>{ev.kind}</strong><span className="event-meta">{formatTs(ev.ts)} · qty {ev.qty}{ev.order_id ? <> · order <span className="mono">{ev.order_id}</span></> : ""}</span>{ev.note && <span className="event-note">{ev.note}</span>}</div></div>
                <div className="event-tags">{reverseIds.has(ev.id) && <span className="tag tag-reverse">{applySucceeded ? "Reversed" : "To reverse"}</span>}{preservedIds.has(ev.id) && <span className="tag tag-preserve">Preserved</span>}<button type="button" className="btn btn-small" onClick={() => openEvent(ev)} disabled={busyAny}>Details</button></div></li>);
            })}</ol>
            {!sortedEvents.length && <p className="muted">No events were returned for this product.</p>}
          </section>
        </>) : !initialLoading && !notSeeded ? (<section className="panel empty"><h2>Incident unavailable</h2><p>{error ?? "The incident could not be loaded."}</p>{isViewer ? <p>An operator must load the synthetic inventory demo before you can inspect it.</p> : <button type="button" className="btn btn-primary" onClick={runReset} disabled={busyAny || !canReset}>Load demo</button>}</section>) : null}
      </main>

      <dialog ref={dialogRef} className="dialog" aria-labelledby="dialog-title" onClose={handleDialogClose} onCancel={handleDialogClose}>
        {dialogMode === "event" && selectedEvent && (<div><div className="dialog-head"><h3 id="dialog-title">Event <span className="mono">#{selectedEvent.id}</span></h3><button type="button" className="btn btn-small" onClick={closeDialog} autoFocus>Close</button></div>
          <dl className="mini"><div><dt>Kind</dt><dd>{selectedEvent.kind}</dd></div><div><dt>Quantity</dt><dd>{selectedEvent.qty}</dd></div><div><dt>Product</dt><dd className="mono">{selectedEvent.product_id}</dd></div><div><dt>Order</dt><dd className="mono">{selectedEvent.order_id ?? "—"}</dd></div><div><dt>Timestamp</dt><dd>{formatTs(selectedEvent.ts)}</dd></div>{selectedEvent.note && <div><dt>Note</dt><dd>{selectedEvent.note}</dd></div>}{selectedEvent.reversed_ids && <div><dt>Reversed IDs</dt><dd className="mono">{selectedEvent.reversed_ids}</dd></div>}{selectedEvent.repair_proposal_id && <div><dt>Repair proposal</dt><dd className="mono">{selectedEvent.repair_proposal_id}</dd></div>}</dl>
          <h4>Raw evidence record</h4><pre className="code">{JSON.stringify(selectedEvent, null, 2)}</pre></div>)}
        {dialogMode === "patch" && (<div><div className="dialog-head"><h3 id="dialog-title">Code patch</h3><button type="button" className="btn btn-small" onClick={closeDialog} autoFocus>Close</button></div>
          <div className="patch" role="document">{patch.split("\n").map((line, i) => (<div key={i} className={line.startsWith("+") && !line.startsWith("+++") ? "line add" : line.startsWith("-") && !line.startsWith("---") ? "line del" : "line"}><code>{line || " "}</code></div>))}</div></div>)}
      </dialog>
    </div>
  );
}
