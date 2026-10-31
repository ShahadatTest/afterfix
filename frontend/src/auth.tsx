import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { apiGet, clearAccessToken, isValidAccessKeyShape, registerUnauthorizedCallback, setAccessToken } from "./api";
import type { AuthMeResponse } from "./types";
import App from "./App";
import ThemeToggle from "./ThemeToggle";

interface AuthContextValue {
  actor: AuthMeResponse | null;
  sessionId: number;
  signIn: (key: string) => Promise<void>;
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const DISCARDED = "That sign-in was discarded because the session changed. Please sign in again.";

function isAuthMe(value: unknown): value is AuthMeResponse {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  const roleOk = (record.actor_id === "operator" || record.actor_id === "viewer") && record.role === record.actor_id;
  return roleOk && typeof record.can_reset === "boolean";
}

function sanitized(error: unknown): Error {
  // Errors reaching here come from api.ts (already friendly/sanitized)
  // or are created in this module; never surface raw server text.
  return error instanceof Error ? error : new Error("Sign-in failed. Please try again.");
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [actor, setActor] = useState<AuthMeResponse | null>(null);
  const [sessionId, setSessionId] = useState(0);
  // Ownership of the latest sign-in attempt. Sign-out and the 401
  // callback advance it, so a stale attempt can neither establish nor
  // clear a newer session/token.
  const attemptRef = useRef(0);
  const sessionRef = useRef(0);

  const handleUnauthorized = useCallback(() => {
    // api.ts has already cleared the current token; invalidate any
    // pending sign-in attempt so its late result is discarded.
    attemptRef.current += 1;
    setActor(null);
  }, []);

  useEffect(() => registerUnauthorizedCallback(handleUnauthorized), [handleUnauthorized]);

  const signOut = useCallback(() => {
    attemptRef.current += 1;
    clearAccessToken();
    setActor(null);
  }, []);

  const signIn = useCallback(async (key: string) => {
    if (!isValidAccessKeyShape(key)) {
      throw new Error("Enter a valid access key (32–128 URL-safe characters: letters, digits, '-' or '_').");
    }
    const attempt = ++attemptRef.current;
    const isOwned = () => attempt === attemptRef.current;
    setAccessToken(key);
    try {
      const me = await apiGet<AuthMeResponse>("/api/auth/me");
      // A superseded attempt must not report success or touch state.
      if (!isOwned()) throw new Error(DISCARDED);
      if (!isAuthMe(me)) {
        clearAccessToken();
        setActor(null);
        throw new Error("Sign-in could not be verified. Please try again.");
      }
      // Assign the session id in the same synchronous transition as the
      // actor, so AuthGate mounts App exactly once for this session.
      sessionRef.current += 1;
      setSessionId(sessionRef.current);
      setActor(me);
    } catch (error) {
      if (!isOwned()) {
        // Stale failure: api.ts may already have cleared the token for a
        // current 401 (and invoked the callback); a newer session, if
        // any, must be left untouched. Rethrow the sanitized error.
        throw sanitized(error);
      }
      clearAccessToken();
      setActor(null);
      throw sanitized(error);
    }
  }, []);

  return <AuthContext.Provider value={{ actor, sessionId, signIn, signOut }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}

const PUBLIC_VIEW: AuthMeResponse = { actor_id: "viewer", role: "viewer", can_reset: false };

function OperatorAccess({ onDone }: { onDone: () => void }) {
  const { signIn, signOut } = useAuth();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busyRef = useRef(false);
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialogRef.current?.showModal();
    return () => { previous?.focus(); };
  }, []);

  function dismiss() {
    // Closing the dialog also invalidates any pending key-verification attempt.
    signOut();
    setKey("");
    onDone();
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      await signIn(key);
      onDone();
    } catch (e) {
      setError(e instanceof Error && e.message !== DISCARDED ? e.message : "Operator access could not be verified. Please try again.");
    } finally {
      setKey("");
      busyRef.current = false;
      setBusy(false);
    }
  }

  return <dialog ref={dialogRef} className="dialog operator-dialog" aria-labelledby="operator-title" onCancel={(event) => { event.preventDefault(); dismiss(); }}>
    <div className="dialog-head"><div><span className="eyebrow">Workspace access</span><h2 id="operator-title">Operator access</h2></div><button type="button" className="btn btn-small" onClick={dismiss}>Close</button></div>
    <p className="auth-copy">Use the operator key to investigate, approve and apply repairs. You can explore the dashboard without a key.</p>
    <p className="auth-warning">Do not enter your Meta API key.</p>
    {error && <div className="banner error" role="alert">{error}</div>}
    <form onSubmit={handleSubmit} className="auth-form">
      <label htmlFor="operator-key">AfterFix operator key</label>
      <input id="operator-key" type="password" maxLength={128} autoComplete="off" spellCheck={false} value={key} onChange={(event) => setKey(event.target.value)} disabled={busy} required autoFocus />
      <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Verifying access…" : "Enable operator access"}</button>
    </form>
  </dialog>;
}

export function AuthGate() {
  const { actor, sessionId, signOut } = useAuth();
  const [accessOpen, setAccessOpen] = useState(false);
  // This is a presentation-only view role. The server independently permits
  // only explicitly enabled public synthetic GET routes; no credential is sent.
  const localAgent = document.querySelector('meta[name="afterfix-workspace"]')?.getAttribute("content") === "connected";
  const displayActor = actor ?? (localAgent ? { ...PUBLIC_VIEW, workspace_mode: "connected" as const } : PUBLIC_VIEW);
  return <>
    <div className="auth-bar">
      <span className="workspace-crumb">Workspace <span aria-hidden="true">/</span><strong>Inventory recovery</strong></span>
      <div className="auth-session"><span className="auth-role">{actor?.role === "operator" ? "Operator" : actor ? "Read-only viewer" : localAgent ? "Private inventory" : "Public view"}</span><ThemeToggle />
        {actor ? <button type="button" className="btn btn-small" onClick={signOut}>{localAgent ? "Lock inventory" : "Return to public view"}</button> : <button type="button" className="btn btn-small" onClick={() => setAccessOpen(true)}>Operator access</button>}
      </div>
    </div>
    {/* Role boundaries remount every local repair/report/approval state. */}
    <App key={`${actor ? "authenticated" : "public"}-${sessionId}`} actor={displayActor} locked={localAgent && !actor} />
    {accessOpen && <OperatorAccess onDone={() => setAccessOpen(false)} />}
  </>;
}
