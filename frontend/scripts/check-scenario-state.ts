import assert from "node:assert/strict";
import { advanceSession, createLabSession, hasAdvanceBinding, hasCanonicalAdvanceBinding, isKnownScenarioId, parseAdvanceResponse, parseCatalogue, parseLoadResponse } from "../src/scenarioState.ts";
import type { LabSession } from "../src/scenarioState.ts";

const hash = "a".repeat(64);
const newHash = "b".repeat(64);
const thirdHash = "c".repeat(64);
const loadId = "123e4567-e89b-42d3-a456-426614174000";
const otherLoadId = "123e4567-e89b-42d3-a456-426614174001";

const catalogue = {
  enabled: true,
  data_label: "Synthetic Scenario Lab",
  can_load: true,
  scenarios: [
    { id: "duplicate_order", title: "Duplicate order", description: "Duplicate demo", can_advance: false },
    { id: "missing_opening", title: "Missing opening", description: "Missing evidence demo", can_advance: false },
    { id: "stale_snapshot", title: "Stale snapshot", description: "Restock demo", can_advance: true },
  ],
};

// Catalogue: exact flags, exactly three unique known IDs, bounded human strings, matching capability.
assert.equal(parseCatalogue(catalogue)?.scenarios.length, 3);
assert.deepEqual(parseCatalogue(catalogue)?.scenarios.map((s) => s.id), ["duplicate_order", "missing_opening", "stale_snapshot"]);
assert.equal(parseCatalogue(null), null);
assert.equal(parseCatalogue([]), null);
assert.equal(parseCatalogue("catalogue"), null);
assert.equal(parseCatalogue({ ...catalogue, enabled: false }), null);
assert.equal(parseCatalogue({ ...catalogue, enabled: 1 }), null);
assert.equal(parseCatalogue({ ...catalogue, enabled: "true" }), null);
assert.equal(parseCatalogue({ ...catalogue, can_load: "true" }), null);
assert.equal(parseCatalogue({ ...catalogue, can_load: 1 }), null);
assert.equal(parseCatalogue({ ...catalogue, can_load: null }), null);
assert.equal(parseCatalogue({ ...catalogue, data_label: "Other" }), null);
assert.equal(parseCatalogue({ ...catalogue, data_label: "Synthetic Scenario Lab\n" }), null);
assert.equal(parseCatalogue({ ...catalogue, scenarios: catalogue.scenarios.slice(0, 2) }), null);
assert.equal(parseCatalogue({ ...catalogue, scenarios: [...catalogue.scenarios, catalogue.scenarios[0]] }), null);
// Length three but containing a duplicate (and therefore missing a required ID).
assert.equal(parseCatalogue({ ...catalogue, scenarios: [catalogue.scenarios[0], catalogue.scenarios[0], catalogue.scenarios[2]] }), null);
assert.equal(parseCatalogue({ ...catalogue, scenarios: [catalogue.scenarios[2], catalogue.scenarios[2], catalogue.scenarios[2]] }), null);
assert.equal(parseCatalogue({ ...catalogue, scenarios: catalogue.scenarios.map((s, i) => (i === 0 ? { ...s, id: "unknown" } : s)) }), null);
assert.equal(parseCatalogue({ ...catalogue, scenarios: catalogue.scenarios.map((s, i) => (i === 0 ? { ...s, id: "DUPLICATE_ORDER" } : s)) }), null);
assert.equal(parseCatalogue({ ...catalogue, scenarios: catalogue.scenarios.map((s, i) => (i === 0 ? { ...s, can_advance: true } : s)) }), null);
assert.equal(parseCatalogue({ ...catalogue, scenarios: catalogue.scenarios.map((s, i) => (i === 2 ? { ...s, can_advance: false } : s)) }), null);
assert.equal(parseCatalogue({ ...catalogue, scenarios: catalogue.scenarios.map((s, i) => (i === 0 ? { ...s, can_advance: "false" } : s)) }), null);
assert.equal(parseCatalogue({ ...catalogue, scenarios: catalogue.scenarios.map((s, i) => (i === 0 ? { ...s, title: "" } : s)) }), null);
assert.equal(parseCatalogue({ ...catalogue, scenarios: catalogue.scenarios.map((s, i) => (i === 0 ? { ...s, title: "   " } : s)) }), null);
assert.equal(parseCatalogue({ ...catalogue, scenarios: catalogue.scenarios.map((s, i) => (i === 0 ? { ...s, title: "x".repeat(121) } : s)) }), null);
assert.equal(parseCatalogue({ ...catalogue, scenarios: catalogue.scenarios.map((s, i) => (i === 0 ? { ...s, title: 42 } : s)) }), null);
assert.equal(parseCatalogue({ ...catalogue, scenarios: catalogue.scenarios.map((s, i) => (i === 0 ? { ...s, description: "" } : s)) }), null);
assert.equal(parseCatalogue({ ...catalogue, scenarios: catalogue.scenarios.map((s, i) => (i === 0 ? { ...s, description: "x".repeat(501) } : s)) }), null);
assert.equal(parseCatalogue({ ...catalogue, scenarios: catalogue.scenarios.map((s, i) => (i === 0 ? { ...s, description: null } : s)) }), null);
assert.equal(parseCatalogue({ ...catalogue, scenarios: "three" }), null);
assert.equal(isKnownScenarioId("stale_snapshot"), true);
assert.equal(isKnownScenarioId("duplicate_order"), true);
assert.equal(isKnownScenarioId("Stale"), false);
assert.equal(isKnownScenarioId(""), false);
assert.equal(isKnownScenarioId(null), false);

// Load: requested binding, canonical scope, lower UUID/hash exact lengths, safe nonnegative stocks.
const load = {
  scenario_id: "stale_snapshot",
  load_id: loadId,
  incident_id: "INC-001",
  product_id: "WIDGET-1",
  snapshot_hash: hash,
  data_label: "Synthetic Scenario Lab",
  widget_stock: 90,
  gadget_stock: 5,
};
assert.deepEqual(parseLoadResponse(load, "stale_snapshot"), load);
assert.deepEqual(parseLoadResponse({ ...load, scenario_id: "duplicate_order" }, "duplicate_order"), { ...load, scenario_id: "duplicate_order" });
assert.equal(parseLoadResponse(load, "duplicate_order"), null);
assert.equal(parseLoadResponse(load, "unknown"), null);
assert.equal(parseLoadResponse(load, null), null);
assert.equal(parseLoadResponse(null, "stale_snapshot"), null);
assert.equal(parseLoadResponse([], "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, incident_id: "INC-002" }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, incident_id: "inc-001" }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, product_id: "OTHER" }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, product_id: "WIDGET-2" }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, data_label: "Other" }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, load_id: loadId.toUpperCase() }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, load_id: loadId + "\n" }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, load_id: loadId.slice(0, 35) }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, load_id: loadId + "0" }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, load_id: loadId.replace("a", "g") }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, snapshot_hash: hash.toUpperCase() }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, snapshot_hash: hash + "\n" }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, snapshot_hash: hash.slice(0, 63) }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, snapshot_hash: hash + "a" }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, snapshot_hash: "z".repeat(64) }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, widget_stock: 1.5 }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, widget_stock: -1 }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, widget_stock: Number.NaN }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, gadget_stock: Number.POSITIVE_INFINITY }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, gadget_stock: Number.NEGATIVE_INFINITY }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, widget_stock: Number.MAX_SAFE_INTEGER + 1 }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, widget_stock: "90" }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, gadget_stock: null }, "stale_snapshot"), null);
assert.equal(parseLoadResponse({ ...load, gadget_stock: true }, "stale_snapshot"), null);
const { gadget_stock: _omitted, ...loadMissingStock } = load;
assert.equal(parseLoadResponse(loadMissingStock, "stale_snapshot"), null);

const session = createLabSession(parseLoadResponse(load, "stale_snapshot"));
assert.ok(session);
assert.equal(session.advanced, false);
assert.equal(session.snapshotHash, hash);
assert.equal(createLabSession(null), null);
assert.equal(createLabSession({ ...load, widget_stock: -1 } as never), null);

// Advance: binding to session/load, expected hash equals current hash, new hash differs, safe values.
const advance = {
  scenario_id: "stale_snapshot",
  load_id: loadId,
  snapshot_hash: newHash,
  incident_id: "INC-001",
  product_id: "WIDGET-1",
  data_label: "Synthetic Scenario Lab",
  stock: 91,
  event_id: 42,
};
assert.deepEqual(parseAdvanceResponse(advance, session, "proposal-1", hash), advance);
assert.equal(hasAdvanceBinding(session, "proposal-1", hash, true, false), true);
assert.equal(hasAdvanceBinding(session, "proposal-1", hash, false, false), false);
assert.equal(hasAdvanceBinding(session, "proposal-1", hash, true, true), false);
assert.equal(hasAdvanceBinding(session, "proposal-1", hash, "true" as never, false), false);
assert.equal(hasAdvanceBinding(session, "proposal-1", hash, true, "false" as never), false);
assert.equal(hasAdvanceBinding(session, "proposal-1", hash, 1 as never, false), false);
assert.equal(hasAdvanceBinding(session, "", hash, true, false), false);
assert.equal(hasAdvanceBinding(session, "   ", hash, true, false), false);
assert.equal(hasAdvanceBinding(session, "x".repeat(201), hash, true, false), false);
assert.equal(hasAdvanceBinding(session, "x".repeat(200), hash, true, false), true);
assert.equal(hasAdvanceBinding(session, null, hash, true, false), false);
assert.equal(hasAdvanceBinding(session, "proposal-1", newHash, true, false), false);
assert.equal(hasAdvanceBinding(session, "proposal-1", hash.toUpperCase(), true, false), false);
assert.equal(hasAdvanceBinding(session, "proposal-1", hash + "\n", true, false), false);
assert.equal(hasAdvanceBinding(null, "proposal-1", hash, true, false), false);
assert.equal(parseAdvanceResponse({ ...advance, snapshot_hash: hash }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, snapshot_hash: newHash.toUpperCase() }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, snapshot_hash: newHash + "\n" }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, load_id: otherLoadId }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, scenario_id: "duplicate_order" }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, incident_id: "INC-002" }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, product_id: "GADGET-1" }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, data_label: "Other" }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse(advance, session, "proposal-1", newHash), null);
assert.equal(parseAdvanceResponse(advance, session, "proposal-1", hash.toUpperCase()), null);
assert.equal(parseAdvanceResponse(advance, session, "", hash), null);
assert.equal(parseAdvanceResponse(advance, session, "   ", hash), null);
assert.equal(parseAdvanceResponse(advance, session, "x".repeat(201), hash), null);
assert.deepEqual(parseAdvanceResponse(advance, session, "x".repeat(200), hash), advance);
assert.equal(parseAdvanceResponse(advance, session, 42, hash), null);
assert.equal(parseAdvanceResponse({ ...advance, event_id: 0 }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, event_id: -3 }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, event_id: 1.5 }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, event_id: Number.NaN }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, event_id: Number.POSITIVE_INFINITY }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, event_id: Number.MAX_SAFE_INTEGER + 1 }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, event_id: "42" }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, stock: -1 }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, stock: 1.5 }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, stock: Number.NaN }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse({ ...advance, stock: "91" }, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse(null, session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse([], session, "proposal-1", hash), null);
assert.equal(parseAdvanceResponse(advance, null, "proposal-1", hash), null);

// advanceSession: applies a validated advance once, and rejects forged/mismatched metadata.
const advanced = advanceSession(session, advance);
assert.equal(advanced?.advanced, true);
assert.equal(advanced?.snapshotHash, newHash);
assert.equal(advanced?.stock, 91);
assert.equal(advanced?.advanceEventId, 42);
assert.equal(session.advanced, false);
assert.equal(parseAdvanceResponse({ ...advance, snapshot_hash: thirdHash }, advanced, "proposal-1", newHash), null);
assert.equal(hasAdvanceBinding(advanced, "proposal-1", newHash, true, false), false);
assert.equal(advanceSession(advanced as LabSession, { ...advance, snapshot_hash: thirdHash }), null);
assert.equal(advanceSession(session, { ...advance, scenario_id: "duplicate_order" } as never), null);
assert.equal(advanceSession(session, { ...advance, load_id: otherLoadId }), null);
assert.equal(advanceSession(session, { ...advance, incident_id: "INC-999" }), null);
assert.equal(advanceSession(session, { ...advance, product_id: "OTHER-1" }), null);
assert.equal(advanceSession(session, { ...advance, data_label: "Forged label" }), null);
assert.equal(advanceSession(session, { ...advance, snapshot_hash: hash }), null);
assert.equal(advanceSession(session, { ...advance, snapshot_hash: newHash.toUpperCase() }), null);
assert.equal(advanceSession(session, { ...advance, snapshot_hash: newHash + "\n" }), null);
assert.equal(advanceSession(session, { ...advance, stock: -1 }), null);
assert.equal(advanceSession(session, { ...advance, stock: Number.MAX_SAFE_INTEGER + 1 }), null);
assert.equal(advanceSession(session, { ...advance, event_id: 0 }), null);
assert.equal(advanceSession(session, { ...advance, event_id: Number.MAX_SAFE_INTEGER + 1 }), null);
assert.equal(advanceSession(session, null as never), null);
// Forged sessions must be rejected defensively rather than trusted or causing a crash.
const forgedSession = { ...session, productId: "OTHER-1" } as LabSession;
assert.equal(parseAdvanceResponse(advance, forgedSession, "proposal-1", hash), null);
assert.equal(advanceSession(forgedSession, advance), null);
assert.equal(hasAdvanceBinding(forgedSession, "proposal-1", hash, true, false), false);
const forgedAdvancedFlag = { ...session, advanced: "false" } as unknown as LabSession;
assert.equal(parseAdvanceResponse(advance, forgedAdvancedFlag, "proposal-1", hash), null);
assert.equal(advanceSession(null as never, advance), null);
assert.equal(advanceSession({} as never, advance), null);
const duplicateSession = createLabSession(parseLoadResponse({ ...load, scenario_id: "duplicate_order" }, "duplicate_order"));
assert.ok(duplicateSession);
assert.equal(parseAdvanceResponse(advance, duplicateSession, "proposal-1", hash), null);
assert.equal(hasAdvanceBinding(duplicateSession, "proposal-1", hash, true, false), false);

// Canonical advance binding: validated stale session + Rust-ready flag + canonical preview scope.
const proposedPreview = {
  proposal: {
    proposal_id: "proposal-1",
    incident_id: "INC-001",
    product_id: "WIDGET-1",
    snapshot_hash: hash,
    status: "proposed",
  },
};
const approvedPreview = {
  proposal: {
    proposal_id: "proposal-2",
    incident_id: "INC-001",
    product_id: "WIDGET-1",
    snapshot_hash: hash,
    status: "approved",
  },
};
assert.equal(hasCanonicalAdvanceBinding(session, proposedPreview, true, false), true);
assert.equal(hasCanonicalAdvanceBinding(session, approvedPreview, true, false), true);
assert.equal(hasCanonicalAdvanceBinding(session, proposedPreview, false, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, proposedPreview, "true" as never, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, proposedPreview, 1 as never, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, proposedPreview, true, true), false);
assert.equal(hasCanonicalAdvanceBinding(session, proposedPreview, true, "false" as never), false);
assert.equal(hasCanonicalAdvanceBinding(session, proposedPreview, true, 0 as never), false);
assert.equal(hasCanonicalAdvanceBinding(null, proposedPreview, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(advanced, proposedPreview, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(duplicateSession, proposedPreview, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(forgedSession, proposedPreview, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(forgedAdvancedFlag, proposedPreview, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(null as never, null, true, false), false);
assert.equal(hasCanonicalAdvanceBinding({} as never, proposedPreview, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, null, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, [], true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, {}, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: null }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: "proposal-1" }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: { ...proposedPreview.proposal, status: "draft" } }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: { ...proposedPreview.proposal, status: "applied" } }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: { ...proposedPreview.proposal, status: "PROPOSED" } }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: { ...proposedPreview.proposal, status: true } }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: { ...proposedPreview.proposal, proposal_id: "" } }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: { ...proposedPreview.proposal, proposal_id: "   " } }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: { ...proposedPreview.proposal, proposal_id: "x".repeat(201) } }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: { ...proposedPreview.proposal, proposal_id: "x".repeat(200) } }, true, false), true);
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: { ...proposedPreview.proposal, proposal_id: 42 } }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: { ...proposedPreview.proposal, incident_id: "INC-002" } }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: { ...proposedPreview.proposal, product_id: "GADGET-1" } }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: { ...proposedPreview.proposal, snapshot_hash: newHash } }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: { ...proposedPreview.proposal, snapshot_hash: hash.toUpperCase() } }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: { ...proposedPreview.proposal, snapshot_hash: hash + "\n" } }, true, false), false);
// Missing canonical scope entirely, and preview-level scope that conflicts with proposal scope.
const { incident_id: _inc, ...proposalWithoutIncident } = proposedPreview.proposal;
assert.equal(hasCanonicalAdvanceBinding(session, { proposal: proposalWithoutIncident }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { incident_id: "INC-002", proposal: proposedPreview.proposal }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { product_id: "OTHER-1", proposal: proposedPreview.proposal }, true, false), false);
assert.equal(hasCanonicalAdvanceBinding(session, { snapshot_hash: newHash, proposal: proposedPreview.proposal }, true, false), false);
// Each missing nested proposal field fails even when the correct equivalent is present at preview level.
const { proposal_id: _pid, ...proposalWithoutId } = proposedPreview.proposal;
assert.equal(hasCanonicalAdvanceBinding(session, { proposal_id: "proposal-1", proposal: proposalWithoutId }, true, false), false);
const { incident_id: _nestedInc, ...proposalWithoutNestedIncident } = proposedPreview.proposal;
assert.equal(hasCanonicalAdvanceBinding(session, { incident_id: "INC-001", proposal: proposalWithoutNestedIncident }, true, false), false);
const { product_id: _prod, ...proposalWithoutProduct } = proposedPreview.proposal;
assert.equal(hasCanonicalAdvanceBinding(session, { product_id: "WIDGET-1", proposal: proposalWithoutProduct }, true, false), false);
const { snapshot_hash: _snap, ...proposalWithoutHash } = proposedPreview.proposal;
assert.equal(hasCanonicalAdvanceBinding(session, { snapshot_hash: hash, proposal: proposalWithoutHash }, true, false), false);
const { status: _status, ...proposalWithoutStatus } = proposedPreview.proposal;
assert.equal(hasCanonicalAdvanceBinding(session, { status: "proposed", proposal: proposalWithoutStatus }, true, false), false);

console.log("scenario-state checks passed");
