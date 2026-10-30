import { useEffect, useRef, useState } from "react";
import { ApiError, apiGet } from "./api";
import type { AuthMeResponse } from "./types";
import { parseCatalogue } from "./scenarioState";
import type { LabSession, ScenarioCatalogue, ScenarioId } from "./scenarioState";
import "./scenario-lab.css";

interface Props {
  actor: AuthMeResponse;
  busy: string | null;
  labSession: LabSession | null;
  advanceReady: boolean;
  hasAppliedRepair: boolean;
  onLoad: (id: ScenarioId) => Promise<void>;
  onAdvance: () => Promise<void>;
}

type CatalogueState = "loading" | "ready" | "error" | "hidden";

const KNOWN_TITLES: Record<ScenarioId, string> = {
  duplicate_order: "Duplicate order",
  missing_opening: "Missing opening evidence",
  stale_snapshot: "Stale snapshot",
};

export default function ScenarioLab({
  actor,
  busy,
  labSession,
  advanceReady,
  hasAppliedRepair,
  onLoad,
  onAdvance,
}: Props) {
  const [catalogue, setCatalogue] = useState<ScenarioCatalogue | null>(null);
  const [state, setState] = useState<CatalogueState>("loading");
  const [selected, setSelected] = useState<ScenarioId | null>(null);
  const [loadConfirmed, setLoadConfirmed] = useState(false);
  const [advanceConfirmed, setAdvanceConfirmed] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);
  const generationRef = useRef(0);

  const busyAny = busy !== null;
  const isOperator = actor.role === "operator";
  const canReset = isOperator && actor.can_reset === true;
  const catalogueCanLoad = catalogue?.can_load === true;

  // Catalogue is fetched only on mount or explicit retry. A generation
  // counter plus the cleanup flag ignore obsolete or unmounted results;
  // the auth layer separately guards the underlying session.
  useEffect(() => {
    const generation = ++generationRef.current;
    let cancelled = false;
    setState("loading");
    setCatalogue(null);
    setSelected(null);
    setLoadConfirmed(false);
    setAdvanceConfirmed(false);
    (async () => {
      try {
        const raw: unknown = await apiGet<unknown>("/api/lab/scenarios");
        if (cancelled || generation !== generationRef.current) return;
        const parsed = parseCatalogue(raw);
        if (!parsed) {
          setState("error");
          return;
        }
        setCatalogue(parsed);
        setState("ready");
      } catch (error) {
        if (cancelled || generation !== generationRef.current) return;
        if (error instanceof ApiError && error.status === 404) {
          setCatalogue(null);
          setState("hidden");
          return;
        }
        setCatalogue(null);
        setState("error");
      }
    })();
    return () => {
      cancelled = true;
      generationRef.current += 1;
    };
  }, [reloadTick]);

  const sessionKey = labSession
    ? `${labSession.scenarioId}:${labSession.loadId}:${String(labSession.advanced)}:${labSession.snapshotHash}`
    : "none";
  const actorKey = `${actor.actor_id}:${actor.role}:${String(actor.can_reset)}`;

  // Neither confirmation may survive a busy transition (start or settle),
  // a selection change, an actor/capability change, or any session change
  // (including load, advance, reset, investigation, approval, apply,
  // verification, sign-out, or another load as reflected through props).
  useEffect(() => {
    setLoadConfirmed(false);
    setAdvanceConfirmed(false);
  }, [busy, selected, actorKey, sessionKey]);

  if (state === "hidden") return null;

  const selectedItem = selected
    ? catalogue?.scenarios.find((scenario) => scenario.id === selected) ?? null
    : null;
  const selectedKnown = selectedItem !== null;
  const canLoad = canReset && catalogueCanLoad && selectedKnown && loadConfirmed && !busyAny;

  const isStaleSession = labSession?.scenarioId === "stale_snapshot";
  const alreadyAdvanced = labSession?.advanced === true;
  const canAdvance =
    canReset &&
    catalogueCanLoad &&
    isStaleSession &&
    !alreadyAdvanced &&
    advanceReady === true &&
    hasAppliedRepair === false &&
    advanceConfirmed &&
    !busyAny;
  const mutationControlsDisabled = busyAny || !canReset;

  const currentTitle = labSession
    ? catalogue?.scenarios.find((scenario) => scenario.id === labSession.scenarioId)?.title ??
      KNOWN_TITLES[labSession.scenarioId]
    : null;

  const handleLoad = () => {
    if (!canLoad || !selected) return;
    const id = selected;
    setLoadConfirmed(false);
    void onLoad(id);
  };

  const handleAdvance = () => {
    if (!canAdvance) return;
    setAdvanceConfirmed(false);
    void onAdvance();
  };

  return (
    <section className="panel scenario-panel" aria-labelledby="scenario-title">
      <div className="panel-head">
        <div>
          <span className="eyebrow">Scenario Lab</span>
          <h2 id="scenario-title">Synthetic scenarios</h2>
        </div>
        <span className="scenario-label">Synthetic Scenario Lab</span>
      </div>

      <p className="scenario-copy">
        This panel offers fixed synthetic demonstrations. Loading or advancing a scenario makes
        no model requests. Running an investigation is a separate explicit action and does use
        model requests. This panel does not grant backend authority; the service makes the final
        decision on every action.
      </p>

      {labSession && currentTitle && (
        <div className="scenario-current" role="status">
          <p className="scenario-current-title">
            Current scenario: <strong>{currentTitle}</strong>
            {alreadyAdvanced ? " — synthetic restock already added." : ""}
          </p>
          <p className="scenario-current-detail">
            Load reference: <span className="scenario-mono">{labSession.loadId}</span>
          </p>
        </div>
      )}

      {state === "loading" && (
        <p className="muted" role="status">
          Loading scenarios…
        </p>
      )}

      {state === "error" && (
        <div className="scenario-error" role="alert">
          <p>Scenarios could not be loaded. No changes were made.</p>
          <button
            type="button"
            className="btn scenario-retry"
            onClick={() => setReloadTick((value) => value + 1)}
            disabled={busyAny}
          >
            Retry loading scenarios
          </button>
        </div>
      )}

      {state === "ready" && catalogue && (
        <>
          <fieldset className="scenario-fieldset">
            <legend>Choose a scenario</legend>
            <div className="scenario-cards">
              {catalogue.scenarios.map((scenario) => (
                <label
                  key={scenario.id}
                  className={`scenario-card${selected === scenario.id ? " scenario-selected" : ""}`}
                >
                  <input
                    type="radio"
                    name="scenario"
                    value={scenario.id}
                    checked={selected === scenario.id}
                    disabled={busyAny}
                    onChange={() => {
                      setSelected(scenario.id);
                      setLoadConfirmed(false);
                      setAdvanceConfirmed(false);
                    }}
                  />
                  <span className="scenario-card-text">
                    <strong>{scenario.title}</strong>
                    <span className="scenario-card-description">{scenario.description}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          {!isOperator && (
            <p className="muted" role="status">
              You have read-only access. You can review the catalogue, but you cannot load or
              advance scenarios.
            </p>
          )}
          {isOperator && !actor.can_reset && (
            <p className="muted" role="status">
              Scenario changes are unavailable because this account does not have reset
              capability.
            </p>
          )}
          {isOperator && actor.can_reset && !catalogueCanLoad && (
            <p className="muted" role="status">
              Scenario loading is currently unavailable.
            </p>
          )}

          <label className="scenario-check">
            <input
              type="checkbox"
              checked={loadConfirmed}
              disabled={mutationControlsDisabled || !catalogueCanLoad}
              onChange={(event) => setLoadConfirmed(event.target.checked)}
            />
            <span>
              I understand that loading the selected scenario replaces the synthetic demo data
              and clears saved cases and approvals.
            </span>
          </label>

          <div className="scenario-actions">
            <button
              type="button"
              className="btn btn-primary scenario-action"
              disabled={!canLoad}
              onClick={handleLoad}
            >
              {busy === "scenario-load" ? "Loading scenario…" : "Load selected scenario"}
            </button>
          </div>
          {!selectedKnown && (
            <p className="muted">Select a scenario before loading. Nothing is selected automatically.</p>
          )}

          {isStaleSession && (
            <div className="scenario-advance">
              <h3>Synthetic restock</h3>
              <p>
                This adds 1 unit of legitimate synthetic restock. It invalidates the current
                preview and any approval. It is not a repair or verification, and it does not
                investigate, approve, or apply anything automatically.
              </p>

              {alreadyAdvanced ? (
                <p className="muted" role="status">
                  The synthetic restock has already been added for this load. A fresh
                  investigation and a current valid preview are needed before any further
                  recovery step.
                </p>
              ) : (
                <>
                  {!advanceReady && (
                    <p className="muted" role="status">
                      A current valid preview is required before this action becomes available.
                    </p>
                  )}
                  {advanceReady && hasAppliedRepair && (
                    <p className="muted" role="status">
                      This action is unavailable because a repair has already been applied.
                    </p>
                  )}
                  <label className="scenario-check">
                    <input
                      type="checkbox"
                      checked={advanceConfirmed}
                      disabled={mutationControlsDisabled || !catalogueCanLoad}
                      onChange={(event) => setAdvanceConfirmed(event.target.checked)}
                    />
                    <span>
                      I understand this adds 1 unit of legitimate synthetic restock, invalidates
                      the current preview and any approval, and is not a repair or verification.
                    </span>
                  </label>
                  <div className="scenario-actions">
                    <button
                      type="button"
                      className="btn scenario-action"
                      disabled={!canAdvance}
                      onClick={handleAdvance}
                    >
                      {busy === "scenario-advance" ? "Adding restock…" : "Add synthetic restock"}
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
