import { useEffect, useState } from "react";
import { apiGet } from "./api";

export interface AgentScan {
  enabled: boolean;
  product_id: string;
  product_name: string;
  events: number;
  stock: number;
  status: "candidate" | "clean" | "needs_review";
  reason: string;
  model_key_configured: boolean;
  runtime_configured: boolean;
}

export default function AgentConnection({ connected, operator, busy, scanning = false, result, onScan }: {
  connected: boolean; operator: boolean; busy: boolean; scanning?: boolean; result: AgentScan | null; onScan: () => void;
}) {
  const [status, setStatus] = useState<AgentScan | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!connected || !operator) return;
    let cancelled = false;
    apiGet<AgentScan>("/api/agent/status").then(value => { if (!cancelled) setStatus(value); })
      .catch(() => { if (!cancelled) setError("Connection could not be checked. Review the local agent configuration."); });
    return () => { cancelled = true; };
  }, [connected, operator]);
  const scan = result ?? status;
  if (!connected) return <details className="agent-install panel">
    <summary><span><span className="eyebrow">Install &amp; connect</span><strong>Use AfterFix with your inventory app</strong></span><span className="agent-chip">Local SQLite agent</span></summary>
    <p>Run a local agent beside a supported inventory SQLite file. It scans recorded events and opens a case for review. Repairs still require investigation, a verified preview and your approval.</p>
    <ol className="agent-steps"><li>Install locally</li><li>Connect inventory &amp; code fix</li><li>Scan &amp; investigate</li><li>Review, approve &amp; verify</li></ol>
    <p className="muted">This website is a shared synthetic demo. Your database stays with your local installation; it is never uploaded here. The first connector supports one selected product and a documented inventory schema.</p>
    <a className="agent-doc-link" href="https://github.com/ShahadatTest/afterfix/blob/master/docs/INSTALL_AGENT.md" target="_blank" rel="noreferrer">Open installation guide <span aria-hidden="true">↗</span></a>
  </details>;
  return <section className="panel agent-connected" aria-labelledby="connection-title">
    <div className="panel-head"><div><span className="eyebrow">Connected inventory</span><h2 id="connection-title">{scan?.product_name ?? "Local SQLite agent"}</h2></div><button type="button" className="btn" onClick={onScan} disabled={busy || !operator}>{scanning ? "Scanning…" : "Scan inventory"}</button></div>
    <p>{scan?.reason ?? (operator ? "Checking the configured inventory connection…" : "An operator manages scans and repairs.")}</p>
    {scan && <div className="agent-facts"><span className="agent-chip">{scan.status === "candidate" ? "Investigation candidate" : scan.status === "clean" ? "No outstanding duplicate found" : "Manual review needed"}</span><span>{scan.events} recorded events</span><span>Scan uses no model requests</span></div>}
    {operator && scan && (!scan.model_key_configured || !scan.runtime_configured) && <p className="banner warn">Scanning is ready. AI investigation needs the server-side Meta key and verified runtime budget. No model request has been started.</p>}
    {error && !result && <p role="alert">{error}</p>}
  </section>;
}
