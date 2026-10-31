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

function SignInForm() {
  const { signIn } = useAuth();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Synchronous mutex: acquired before the first await, so a second
  // submit/Enter in the same tick cannot start a parallel sign-in.
  const busyRef = useRef(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      await signIn(key);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign-in failed. Please try again.");
    } finally {
      // The access key never persists after success or failure.
      setKey("");
      busyRef.current = false;
      setBusy(false);
    }
  }

  return (
    <main className="auth-shell">
      <div className="auth-theme"><ThemeToggle /></div>
      <aside className="auth-intro" aria-label="About AfterFix">
        <div className="brand"><span className="brand-mark" aria-hidden="true">A<span className="brand-dot" /></span><div><strong>AfterFix</strong><span>Evidence-driven recovery</span></div></div>
        <div className="auth-intro-copy"><span className="eyebrow">From incident to resolution</span><h2>Fix the data.<br />Preserve what’s right.</h2><p>A focused workspace for investigating an incident and reviewing a bounded repair.</p></div>
        <ol className="auth-process"><li><span>01</span><div><strong>Investigate</strong><p>Trace the patch and recorded evidence.</p></div></li><li><span>02</span><div><strong>Review &amp; approve</strong><p>Inspect the preview before a separate apply.</p></div></li><li><span>03</span><div><strong>Verify</strong><p>Check the outcome and preserved activity.</p></div></li></ol>
        <p className="auth-intro-foot">Synthetic inventory demo · Human approval required</p>
      </aside>
      <section className="auth-card" aria-labelledby="signin-title">
        <span className="eyebrow">Recovery workspace</span>
        <h1 id="signin-title">Sign in</h1>
        <p className="auth-copy">Use the access key provided by the demo operator.</p>
        <p className="auth-warning">Do not enter your Meta API key.</p>
        {error && <div className="banner error" role="alert">{error}</div>}
        <form onSubmit={handleSubmit} className="auth-form">
          <label htmlFor="access-key">AfterFix access key</label>
          <input id="access-key" type="password" maxLength={128} autoComplete="off" spellCheck={false} value={key} onChange={(e) => setKey(e.target.value)} disabled={busy} required />
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
      </section>
    </main>
  );
}

export function AuthGate() {
  const { actor, sessionId, signOut } = useAuth();

  if (!actor) return <SignInForm />;

  const isViewer = actor.role === "viewer";
  return (
    <>
      <div className="auth-bar">
        <span className="workspace-crumb">Workspace <span aria-hidden="true">/</span> <strong>Inventory recovery</strong></span>
        <div className="auth-session"><span className="auth-role">{isViewer ? "Read-only viewer" : "Operator"}</span><ThemeToggle />
        <button type="button" className="btn btn-small" onClick={signOut}>Sign out</button>
        </div>
      </div>
      {/* sessionId changes only once, synchronously with an accepted
          sign-in, so each session gets exactly one fresh App mount. */}
      <App key={sessionId} actor={actor} />
    </>
  );
}
