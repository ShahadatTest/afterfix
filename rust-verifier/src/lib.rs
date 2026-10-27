use serde::{Deserialize, Deserializer, Serialize};
use std::collections::{HashMap, HashSet};
use std::fmt;

pub const INPUT_LIMIT: usize = 524_288;
const MAX_ITEMS: usize = 200;
const MAX_STRING: usize = 128;

fn req_opt<'de, D, T>(d: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(d)
}

fn de_kind<'de, D>(d: D) -> Result<Kind, D::Error>
where
    D: Deserializer<'de>,
{
    struct V;
    impl<'de> serde::de::Visitor<'de> for V {
        type Value = Kind;
        fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
            f.write_str("kind string")
        }
        fn visit_str<E: serde::de::Error>(self, s: &str) -> Result<Kind, E> {
            match s {
                "init" => Ok(Kind::Init),
                "restock" => Ok(Kind::Restock),
                "order_deduction" => Ok(Kind::OrderDeduction),
                "repair_credit" => Ok(Kind::RepairCredit),
                _ => Err(E::custom("unsupported kind")),
            }
        }
        fn visit_string<E: serde::de::Error>(self, s: String) -> Result<Kind, E> {
            self.visit_str(&s)
        }
    }
    // deserialize_str rejects maps/sequences/numbers/bools/null for this field.
    d.deserialize_str(V)
}

fn de_status<'de, D>(d: D) -> Result<Status, D::Error>
where
    D: Deserializer<'de>,
{
    struct V;
    impl<'de> serde::de::Visitor<'de> for V {
        type Value = Status;
        fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
            f.write_str("status string")
        }
        fn visit_str<E: serde::de::Error>(self, s: &str) -> Result<Status, E> {
            match s {
                "applied" => Ok(Status::Applied),
                _ => Err(E::custom("unsupported status")),
            }
        }
        fn visit_string<E: serde::de::Error>(self, s: String) -> Result<Status, E> {
            self.visit_str(&s)
        }
    }
    d.deserialize_str(V)
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub protocol_version: i64,
    pub product_id: String,
    pub recorded_stock: i64,
    pub events: Vec<Event>,
    pub applied_proposals: Vec<Applied>,
    #[serde(deserialize_with = "req_opt")]
    pub proposal: Option<Proposal>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Event {
    pub id: i64,
    pub product_id: String,
    #[serde(deserialize_with = "de_kind")]
    pub kind: Kind,
    pub qty: i64,
    #[serde(deserialize_with = "req_opt")]
    pub order_id: Option<String>,
    pub reversed_ids: Vec<i64>,
    #[serde(deserialize_with = "req_opt")]
    pub repair_proposal_id: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Init,
    Restock,
    OrderDeduction,
    RepairCredit,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Applied {
    pub proposal_id: String,
    pub product_id: String,
    pub credit_qty: i64,
    pub reverse_event_ids: Vec<i64>,
    #[serde(deserialize_with = "de_status")]
    pub status: Status,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Applied,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Proposal {
    pub credit_qty: i64,
    pub reverse_event_ids: Vec<i64>,
}

#[derive(Debug, Serialize, PartialEq)]
pub struct Output {
    pub protocol_version: i64,
    pub verifier_version: &'static str,
    pub ok: bool,
    pub errors: Vec<&'static str>,
    pub ledger_actual: Option<i64>,
    pub expected_stock: Option<i64>,
    pub outstanding_credit: Option<i64>,
    pub reverse_event_ids: Vec<i64>,
    pub proposal_matches: Option<bool>,
}

impl Output {
    fn fail(code: &'static str, candidate: bool) -> Self {
        Output {
            protocol_version: 1,
            verifier_version: "0.1.0",
            ok: false,
            errors: vec![code],
            ledger_actual: None,
            expected_stock: None,
            outstanding_credit: None,
            reverse_event_ids: Vec::new(),
            proposal_matches: if candidate { Some(false) } else { None },
        }
    }
    fn protocol(code: &'static str) -> Self {
        Self::fail(code, false)
    }
}

struct Failure {
    code: &'static str,
    exit: i32,
    candidate: bool,
}
fn dom(code: &'static str, candidate: bool) -> Failure {
    Failure {
        code,
        exit: 0,
        candidate,
    }
}
fn proto(code: &'static str) -> Failure {
    Failure {
        code,
        exit: 2,
        candidate: false,
    }
}

fn valid_string(s: &str) -> bool {
    !s.is_empty() && s.len() <= MAX_STRING && s.bytes().all(|b| b >= 0x20 && b != 0x7f)
}

fn check_strings(req: &Request) -> Result<(), Failure> {
    if !valid_string(&req.product_id) {
        return Err(proto("invalid_string"));
    }
    for e in &req.events {
        if !valid_string(&e.product_id) {
            return Err(proto("invalid_string"));
        }
        if let Some(v) = &e.order_id {
            if !valid_string(v) {
                return Err(proto("invalid_string"));
            }
        }
        if let Some(v) = &e.repair_proposal_id {
            if !valid_string(v) {
                return Err(proto("invalid_string"));
            }
        }
    }
    for a in &req.applied_proposals {
        if !valid_string(&a.proposal_id) || !valid_string(&a.product_id) {
            return Err(proto("invalid_string"));
        }
    }
    Ok(())
}

fn protocol_checks(req: &Request) -> Result<(), Failure> {
    if req.protocol_version != 1 {
        return Err(proto("protocol_error"));
    }
    if req.events.len() > MAX_ITEMS || req.applied_proposals.len() > MAX_ITEMS {
        return Err(proto("limit_exceeded"));
    }
    if let Some(p) = &req.proposal {
        if p.reverse_event_ids.len() > MAX_ITEMS {
            return Err(proto("limit_exceeded"));
        }
    }
    for e in &req.events {
        if e.reversed_ids.len() > MAX_ITEMS || e.id <= 0 || e.reversed_ids.iter().any(|x| *x <= 0) {
            return Err(proto(
                if e.id <= 0 || e.reversed_ids.iter().any(|x| *x <= 0) {
                    "invalid_id"
                } else {
                    "limit_exceeded"
                },
            ));
        }
    }
    for a in &req.applied_proposals {
        if a.reverse_event_ids.len() > MAX_ITEMS || a.reverse_event_ids.iter().any(|x| *x <= 0) {
            return Err(proto(if a.reverse_event_ids.iter().any(|x| *x <= 0) {
                "invalid_id"
            } else {
                "limit_exceeded"
            }));
        }
    }
    if let Some(p) = &req.proposal {
        if p.reverse_event_ids.iter().any(|x| *x <= 0) {
            return Err(proto("invalid_id"));
        }
    }
    for w in req.events.windows(2) {
        if w[1].id <= w[0].id {
            return Err(proto("event_order"));
        }
    }
    check_strings(req)
}

fn add(a: i64, b: i64, cand: bool) -> Result<i64, Failure> {
    a.checked_add(b).ok_or_else(|| dom("overflow", cand))
}
fn sub(a: i64, b: i64, cand: bool) -> Result<i64, Failure> {
    a.checked_sub(b).ok_or_else(|| dom("overflow", cand))
}

fn sorted_unique(ids: &[i64]) -> Option<Vec<i64>> {
    let mut v = ids.to_vec();
    v.sort_unstable();
    if v.windows(2).any(|w| w[0] == w[1]) {
        None
    } else {
        Some(v)
    }
}

fn verify(req: &Request) -> Result<Output, Failure> {
    protocol_checks(req)?;
    let cand = req.proposal.is_some();
    let d = |code| dom(code, cand);
    if req.events.is_empty() {
        return Err(d("missing_init"));
    }
    if !req.events.iter().any(|e| e.kind == Kind::Init) {
        return Err(d("missing_init"));
    }
    if req.events.iter().any(|e| e.qty <= 0) {
        return Err(d("nonpositive_qty"));
    }
    if req.applied_proposals.iter().any(|a| a.credit_qty <= 0) {
        return Err(d("nonpositive_qty"));
    }
    if req.events.iter().any(|e| e.product_id != req.product_id) {
        return Err(d("product_mismatch"));
    }
    for e in &req.events {
        match e.kind {
            Kind::OrderDeduction => {
                if e.order_id.as_deref().map_or(true, |s| s.is_empty()) {
                    return Err(d("order_id_rule"));
                }
            }
            _ => {
                if e.order_id.is_some() {
                    return Err(d("order_id_rule"));
                }
            }
        }
        if e.kind != Kind::RepairCredit
            && (!e.reversed_ids.is_empty() || e.repair_proposal_id.is_some())
        {
            return Err(d("repair_ref_rule"));
        }
    }
    let mut by_order: HashMap<&str, Vec<&Event>> = HashMap::new();
    let mut ded_by_id: HashMap<i64, &Event> = HashMap::new();
    for e in req.events.iter().filter(|e| e.kind == Kind::OrderDeduction) {
        by_order
            .entry(e.order_id.as_deref().unwrap())
            .or_default()
            .push(e);
        ded_by_id.insert(e.id, e);
    }
    let mut extra_qty: HashMap<i64, i64> = HashMap::new();
    let mut one_per_order: i64 = 0;
    for group in by_order.values() {
        if group.iter().any(|e| e.qty != group[0].qty) {
            return Err(d("conflicting_qty"));
        }
        one_per_order = add(one_per_order, group[0].qty, cand)?;
        for extra in &group[1..] {
            extra_qty.insert(extra.id, extra.qty);
        }
    }
    let mut init = 0i64;
    let mut restock = 0i64;
    let mut repair = 0i64;
    let mut all_ded = 0i64;
    for e in &req.events {
        let slot = match e.kind {
            Kind::Init => &mut init,
            Kind::Restock => &mut restock,
            Kind::RepairCredit => &mut repair,
            Kind::OrderDeduction => &mut all_ded,
        };
        *slot = add(*slot, e.qty, cand)?;
    }
    let ledger = sub(add(add(init, restock, cand)?, repair, cand)?, all_ded, cand)?;
    if ledger != req.recorded_stock {
        return Err(d("stock_mismatch"));
    }
    let mut applied_by_id: HashMap<&str, &Applied> = HashMap::new();
    for a in &req.applied_proposals {
        if a.product_id != req.product_id {
            return Err(d("repair_linkage"));
        }
        if applied_by_id.insert(a.proposal_id.as_str(), a).is_some() {
            return Err(d("repair_linkage"));
        }
    }
    let mut repaired: HashSet<i64> = HashSet::new();
    let mut repair_pids: HashSet<&str> = HashSet::new();
    for e in req.events.iter().filter(|e| e.kind == Kind::RepairCredit) {
        let pid = e
            .repair_proposal_id
            .as_deref()
            .ok_or_else(|| d("repair_linkage"))?;
        if !repair_pids.insert(pid) {
            return Err(d("repair_linkage"));
        }
        let refs = sorted_unique(&e.reversed_ids).ok_or_else(|| d("invalid_repair_ref"))?;
        if refs.is_empty() {
            return Err(d("invalid_repair_ref"));
        }
        let mut sum = 0i64;
        for r in &refs {
            let target = ded_by_id.get(r).ok_or_else(|| d("invalid_repair_ref"))?;
            if !extra_qty.contains_key(r) || *r >= e.id || target.product_id != req.product_id {
                return Err(d("invalid_repair_ref"));
            }
            if !repaired.insert(*r) {
                return Err(d("invalid_repair_ref"));
            }
            sum = add(sum, target.qty, cand)?;
        }
        if sum != e.qty {
            return Err(d("repair_credit_mismatch"));
        }
        let a = applied_by_id.get(pid).ok_or_else(|| d("repair_linkage"))?;
        let arefs = sorted_unique(&a.reverse_event_ids).ok_or_else(|| d("repair_linkage"))?;
        if a.credit_qty != e.qty || arefs != refs {
            return Err(d("repair_linkage"));
        }
    }
    if applied_by_id.len() != repair_pids.len()
        || !applied_by_id.keys().all(|k| repair_pids.contains(k))
    {
        return Err(d("repair_linkage"));
    }
    let mut outstanding_ids: Vec<i64> = extra_qty
        .keys()
        .filter(|id| !repaired.contains(id))
        .copied()
        .collect();
    outstanding_ids.sort_unstable();
    let mut credit = 0i64;
    for id in &outstanding_ids {
        credit = add(credit, extra_qty[id], cand)?;
    }
    let expected = add(req.recorded_stock, credit, cand)?;
    let deduped_ledger = sub(add(init, restock, cand)?, one_per_order, cand)?;
    if expected != deduped_ledger {
        return Err(d("ledger_mismatch"));
    }
    // proposal:null is valid analysis-only: independently computed totals/refs above are returned.
    match &req.proposal {
        None => {}
        Some(p) => {
            if outstanding_ids.is_empty() || p.credit_qty <= 0 || p.credit_qty != credit {
                return Err(d("candidate_mismatch"));
            }
            match sorted_unique(&p.reverse_event_ids) {
                Some(v) if v == outstanding_ids => {}
                _ => return Err(d("candidate_mismatch")),
            }
        }
    }
    Ok(Output {
        protocol_version: 1,
        verifier_version: "0.1.0",
        ok: true,
        errors: Vec::new(),
        ledger_actual: Some(ledger),
        expected_stock: Some(expected),
        outstanding_credit: Some(credit),
        reverse_event_ids: outstanding_ids,
        proposal_matches: req.proposal.as_ref().map(|_| true),
    })
}

/// Version 1 requires JSON objects for Request/Event/Applied/Proposal.
/// Serde derives would otherwise accept structs encoded as JSON arrays.
/// This shape-only preflight runs on the `Value` parse; the typed decode
/// below still uses the original bytes so duplicate keys are rejected by
/// the derive rather than collapsed by `Value`.
fn object_shape_preflight(value: &serde_json::Value) -> bool {
    let Some(root) = value.as_object() else {
        return false;
    };
    let Some(events) = root.get("events").and_then(serde_json::Value::as_array) else {
        return false;
    };
    if !events.iter().all(serde_json::Value::is_object) {
        return false;
    }
    let Some(applied) = root
        .get("applied_proposals")
        .and_then(serde_json::Value::as_array)
    else {
        return false;
    };
    if !applied.iter().all(serde_json::Value::is_object) {
        return false;
    }
    matches!(
        root.get("proposal"),
        Some(serde_json::Value::Null) | Some(serde_json::Value::Object(_))
    )
}

/// Returns (single-line JSON without newline, process exit code).
pub fn handle_bytes(bytes: &[u8]) -> (String, i32) {
    if bytes.len() > INPUT_LIMIT {
        return render(Output::protocol("limit_exceeded"), 2);
    }
    // Bounded by the input cap above and serde_json recursion limits.
    let value: serde_json::Value = match serde_json::from_slice(bytes) {
        Ok(v) => v,
        Err(_) => return render(Output::protocol("decode_error"), 2),
    };
    if !object_shape_preflight(&value) {
        return render(Output::protocol("decode_error"), 2);
    }
    // Deserialize the original bytes (not the Value) so duplicate keys,
    // unknown fields and all other strict typed checks are still enforced.
    let req: Request = match serde_json::from_slice(bytes) {
        Ok(v) => v,
        Err(_) => return render(Output::protocol("decode_error"), 2),
    };
    match verify(&req) {
        Ok(out) => render(out, 0),
        Err(f) => render(Output::fail(f.code, f.candidate), f.exit),
    }
}

fn render(out: Output, exit: i32) -> (String, i32) {
    let s = serde_json::to_string(&out).unwrap_or_else(|_| {
        "{\"protocol_version\":1,\"verifier_version\":\"0.1.0\",\"ok\":false,\"errors\":[\"decode_error\"],\"ledger_actual\":null,\"expected_stock\":null,\"outstanding_credit\":null,\"reverse_event_ids\":[],\"proposal_matches\":null}".to_string()
    });
    (s, exit)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};

    fn ev(id: i64, kind: &str, qty: i64, order: Value, refs: Value, pid: Value) -> Value {
        json!({"id":id,"product_id":"P1","kind":kind,"qty":qty,"order_id":order,"reversed_ids":refs,"repair_proposal_id":pid})
    }
    fn base() -> Value {
        json!({"protocol_version":1,"product_id":"P1","recorded_stock":100,
          "events":[ev(1,"init",100,Value::Null,json!([]),Value::Null),
                    ev(2,"order_deduction",10,json!("A"),json!([]),Value::Null),
                    ev(3,"order_deduction",10,json!("A"),json!([]),Value::Null),
                    ev(4,"restock",20,Value::Null,json!([]),Value::Null)],
          "applied_proposals":[],
          "proposal":{"credit_qty":10,"reverse_event_ids":[3]}})
    }
    fn with_repair() -> Value {
        let mut v = base();
        v["recorded_stock"] = json!(110);
        v["events"].as_array_mut().unwrap().push(ev(
            5,
            "repair_credit",
            10,
            Value::Null,
            json!([3]),
            json!("r1"),
        ));
        v["applied_proposals"] = json!([{"proposal_id":"r1","product_id":"P1","credit_qty":10,"reverse_event_ids":[3],"status":"applied"}]);
        v["proposal"] = Value::Null;
        v
    }
    fn run(v: &Value) -> (Value, i32) {
        let (s, c) = handle_bytes(serde_json::to_string(v).unwrap().as_bytes());
        (serde_json::from_str(&s).unwrap(), c)
    }
    fn run_raw(s: &str) -> (Value, i32) {
        let (o, c) = handle_bytes(s.as_bytes());
        (serde_json::from_str(&o).unwrap(), c)
    }
    fn assert_domain(v: &Value, code: &str) {
        let (o, c) = run(v);
        assert_eq!(c, 0, "{o}");
        assert_eq!(o["ok"], json!(false), "{o}");
        assert_eq!(o["errors"], json!([code]), "{o}");
        assert_eq!(o["ledger_actual"], Value::Null);
    }
    fn assert_proto(v: &Value, code: &str) {
        let (o, c) = run(v);
        assert_eq!(c, 2, "{o}");
        assert_eq!(o["errors"], json!([code]), "{o}");
        assert_eq!(o["proposal_matches"], Value::Null);
    }
    #[test]
    fn canonical_and_varied_numbers() {
        let (o, c) = run(&base());
        assert_eq!(c, 0);
        assert_eq!(o["ok"], json!(true));
        assert_eq!(o["ledger_actual"], json!(100));
        assert_eq!(o["expected_stock"], json!(110));
        assert_eq!(o["outstanding_credit"], json!(10));
        assert_eq!(o["reverse_event_ids"], json!([3]));
        assert_eq!(o["proposal_matches"], json!(true));
        let mut v = base();
        v["recorded_stock"] = json!(77);
        v["events"][0]["qty"] = json!(80);
        v["events"][1]["qty"] = json!(7);
        v["events"][2]["qty"] = json!(7);
        v["events"][3]["qty"] = json!(11);
        v["proposal"]["credit_qty"] = json!(7);
        let (o, _) = run(&v);
        assert_eq!(o["expected_stock"], json!(84));
    }
    #[test]
    fn clean_needs_null_candidate() {
        let mut v = base();
        v["events"].as_array_mut().unwrap().remove(2);
        v["recorded_stock"] = json!(110);
        v["proposal"] = Value::Null;
        let (o, c) = run(&v);
        assert_eq!((c, o["ok"].as_bool()), (0, Some(true)));
        assert_eq!(o["proposal_matches"], Value::Null);
        v["proposal"] = json!({"credit_qty":10,"reverse_event_ids":[3]});
        let (o, _) = run(&v);
        assert_eq!(o["ok"], json!(false));
        assert_eq!(o["proposal_matches"], json!(false));
    }
    #[test]
    fn null_candidate_with_outstanding_computed() {
        let mut v = base();
        v["proposal"] = Value::Null;
        let (o, c) = run(&v);
        assert_eq!(c, 0);
        assert_eq!(o["ok"], json!(true));
        assert_eq!(o["ledger_actual"], json!(100));
        assert_eq!(o["expected_stock"], json!(110));
        assert_eq!(o["outstanding_credit"], json!(10));
        assert_eq!(o["reverse_event_ids"], json!([3]));
        assert_eq!(o["proposal_matches"], Value::Null);
    }
    #[test]
    fn full_prior_repair_succeeds_without_candidate() {
        let (o, _) = run(&with_repair());
        assert_eq!(o["ok"], json!(true));
        assert_eq!(o["expected_stock"], json!(110));
        assert_eq!(o["outstanding_credit"], json!(0));
    }
    #[test]
    fn failures_clear_numbers() {
        for mutate in [
            |v: &mut Value| v["recorded_stock"] = json!(99),
            |v: &mut Value| v["events"][2]["qty"] = json!(11),
            |v: &mut Value| v["events"][0]["kind"] = json!("restock"),
            |v: &mut Value| v["events"][1]["qty"] = json!(0),
            |v: &mut Value| v["events"][1]["product_id"] = json!("PX"),
            |v: &mut Value| v["proposal"]["reverse_event_ids"] = json!([2]),
        ] {
            let mut v = base();
            mutate(&mut v);
            let (o, c) = run(&v);
            assert_eq!(c, 0, "{o}");
            assert_eq!(o["ok"], json!(false), "{o}");
            assert_eq!(o["ledger_actual"], Value::Null);
            assert_eq!(o["reverse_event_ids"], json!([]));
        }
    }
    #[test]
    fn overflow_is_domain_failure() {
        let mut v = base();
        v["events"][0]["qty"] = json!(i64::MAX);
        v["recorded_stock"] = json!(i64::MAX);
        let (o, c) = run(&v);
        assert_eq!(c, 0);
        assert_eq!(o["ok"], json!(false));
        assert_eq!(o["errors"], json!(["overflow"]));
    }
    #[test]
    fn decode_missing_nullable_and_unknown() {
        let mut v = base();
        v.as_object_mut().unwrap().remove("proposal");
        let (_, c) = run(&v);
        assert_eq!(c, 2);
        let mut v = base();
        v["extra"] = json!(1);
        let (_, c) = run(&v);
        assert_eq!(c, 2);
        let (s, c) = handle_bytes(b"{\"protocol_version\":true}");
        assert_eq!(c, 2);
        assert!(s.len() < 16384);
    }
    #[test]
    fn strict_string_kind_status_on_valid_full_request() {
        for bad in [
            json!({"init":null}),
            json!(["init"]),
            json!(1),
            json!(true),
            json!("unknown_kind"),
        ] {
            let mut v = base();
            v["events"][0]["kind"] = bad;
            assert_proto(&v, "decode_error");
        }
        for bad in [
            json!({"applied":null}),
            json!(["applied"]),
            json!(1),
            json!("pending"),
        ] {
            let mut v = with_repair();
            v["applied_proposals"][0]["status"] = bad;
            assert_proto(&v, "decode_error");
        }
        // Exact strings remain valid.
        let (o, c) = run(&with_repair());
        assert_eq!((c, o["ok"].as_bool()), (0, Some(true)));
    }
    #[test]
    fn numeric_field_types_and_i64_bounds_exit2() {
        // bool / float / numeric-string on otherwise valid full requests.
        for bad in [json!(true), json!(1.5), json!("10")] {
            let mut v = base();
            v["events"][1]["qty"] = bad.clone();
            assert_proto(&v, "decode_error");
            let mut v = base();
            v["recorded_stock"] = bad.clone();
            assert_proto(&v, "decode_error");
            let mut v = base();
            v["protocol_version"] = bad.clone();
            assert_proto(&v, "decode_error");
            let mut v = base();
            v["events"][0]["id"] = bad.clone();
            assert_proto(&v, "decode_error");
            let mut v = base();
            v["proposal"]["credit_qty"] = bad;
            assert_proto(&v, "decode_error");
        }
        let s = serde_json::to_string(&base()).unwrap();
        for raw in [
            s.replacen(
                "\"recorded_stock\":100",
                "\"recorded_stock\":9223372036854775808",
                1,
            ),
            s.replacen(
                "\"recorded_stock\":100",
                "\"recorded_stock\":-9223372036854775809",
                1,
            ),
            s.replacen("\"qty\":100", "\"qty\":9223372036854775808", 1),
            s.replacen("\"id\":1", "\"id\":-9223372036854775809", 1),
        ] {
            let (o, c) = run_raw(&raw);
            assert_eq!(c, 2, "{o}");
            assert_eq!(o["errors"], json!(["decode_error"]));
        }
    }
    #[test]
    fn missing_event_nullable_fields_exit2_but_nulls_valid() {
        for field in [
            "order_id",
            "reversed_ids",
            "repair_proposal_id",
            "kind",
            "qty",
            "id",
            "product_id",
        ] {
            let mut v = base();
            v["events"][0].as_object_mut().unwrap().remove(field);
            assert_proto(&v, "decode_error");
        }
        for field in [
            "proposal_id",
            "product_id",
            "credit_qty",
            "reverse_event_ids",
            "status",
        ] {
            let mut v = with_repair();
            v["applied_proposals"][0]
                .as_object_mut()
                .unwrap()
                .remove(field);
            assert_proto(&v, "decode_error");
        }
        // Explicit nulls already exercised by base()/with_repair() are valid.
        let (o, c) = run(&base());
        assert_eq!((c, o["ok"].as_bool()), (0, Some(true)));
    }
    #[test]
    fn id_order_and_limits_exit2() {
        let mut v = base();
        v["events"][1]["id"] = json!(1);
        assert_proto(&v, "event_order"); // duplicate id
        let mut v = base();
        v["events"][3]["id"] = json!(2);
        assert_proto(&v, "event_order"); // non-ascending
        let mut v = base();
        v["events"][0]["id"] = json!(0);
        assert_proto(&v, "invalid_id");
        let mut v = base();
        v["events"][0]["id"] = json!(-1);
        assert_proto(&v, "invalid_id");
        let mut v = base();
        v["proposal"]["reverse_event_ids"] = json!((1..=201).collect::<Vec<i64>>());
        assert_proto(&v, "limit_exceeded");
        let mut v = base();
        v["events"][0]["reversed_ids"] = json!((1..=201).collect::<Vec<i64>>());
        assert_proto(&v, "limit_exceeded");
        let mut v = with_repair();
        v["applied_proposals"][0]["reverse_event_ids"] = json!((1..=201).collect::<Vec<i64>>());
        assert_proto(&v, "limit_exceeded");
        let mut v = base();
        v["events"] = json!((1..=201)
            .map(|i| ev(i, "init", 1, Value::Null, json!([]), Value::Null))
            .collect::<Vec<_>>());
        assert_proto(&v, "limit_exceeded");
        let mut v = base();
        v["product_id"] = json!("a".repeat(129));
        assert_proto(&v, "invalid_string");
        let mut v = base();
        v["events"][1]["order_id"] = json!("a".repeat(129));
        assert_proto(&v, "invalid_string");
        // Exactly 128 bytes is allowed as a string (then domain product check is not reached for product_id itself).
        let mut v = base();
        v["product_id"] = json!("a".repeat(128));
        for e in v["events"].as_array_mut().unwrap() {
            e["product_id"] = json!("a".repeat(128));
        }
        let (o, c) = run(&v);
        assert_eq!((c, o["ok"].as_bool()), (0, Some(true)), "{o}");
    }
    #[test]
    fn applied_cross_product_duplicate_missing_unused_mismatch() {
        let mut v = with_repair();
        v["applied_proposals"][0]["product_id"] = json!("OTHER");
        assert_domain(&v, "repair_linkage");
        let mut v = with_repair();
        let dup = v["applied_proposals"][0].clone();
        v["applied_proposals"].as_array_mut().unwrap().push(dup);
        assert_domain(&v, "repair_linkage");
        let mut v = with_repair();
        v["applied_proposals"] = json!([]);
        assert_domain(&v, "repair_linkage");
        let mut v = with_repair();
        v["applied_proposals"][0]["reverse_event_ids"] = json!([2]);
        assert_domain(&v, "repair_linkage");
        let mut v = with_repair();
        v["applied_proposals"][0]["credit_qty"] = json!(9);
        assert_domain(&v, "repair_linkage");
        let mut v = with_repair();
        v["applied_proposals"][0]["reverse_event_ids"] = json!([3, 3]);
        assert_domain(&v, "repair_linkage");
        // Duplicate repair_proposal_id across two repair events (second otherwise sums correctly is impossible for same extra, duplicate pid is checked first).
        let mut v = with_repair();
        v["events"].as_array_mut().unwrap().push(ev(
            6,
            "repair_credit",
            10,
            Value::Null,
            json!([3]),
            json!("r1"),
        ));
        v["recorded_stock"] = json!(120);
        assert_domain(&v, "repair_linkage");
    }
    #[test]
    fn candidate_duplicate_incomplete_extra_and_clean_nonnull() {
        for refs in [
            json!([3, 3]),
            json!([]),
            json!([3, 2]),
            json!([3, 99]),
            json!([2]),
        ] {
            let mut v = base();
            v["proposal"]["reverse_event_ids"] = refs;
            let (o, c) = run(&v);
            assert_eq!(c, 0, "{o}");
            assert_eq!(o["errors"], json!(["candidate_mismatch"]), "{o}");
            assert_eq!(o["proposal_matches"], json!(false));
        }
        let mut v = base();
        v["proposal"]["credit_qty"] = json!(0);
        v["proposal"]["reverse_event_ids"] = json!([]);
        assert_domain(&v, "candidate_mismatch");
        let mut v = base();
        v["proposal"]["credit_qty"] = json!(-10);
        assert_domain(&v, "candidate_mismatch");
    }
    #[test]
    fn multiple_init_checked_total_and_near_boundary() {
        let mut v = base();
        v["events"][0]["qty"] = json!(60);
        v["events"]
            .as_array_mut()
            .unwrap()
            .insert(1, ev(5, "init", 40, Value::Null, json!([]), Value::Null));
        // Renumber to keep strictly ascending ids: rebuild events.
        v["events"] = json!([
            ev(1, "init", 60, Value::Null, json!([]), Value::Null),
            ev(2, "init", 40, Value::Null, json!([]), Value::Null),
            ev(3, "order_deduction", 10, json!("A"), json!([]), Value::Null),
            ev(4, "order_deduction", 10, json!("A"), json!([]), Value::Null),
            ev(5, "restock", 20, Value::Null, json!([]), Value::Null)
        ]);
        v["proposal"] = json!({"credit_qty":10,"reverse_event_ids":[4]});
        let (o, c) = run(&v);
        assert_eq!((c, o["ok"].as_bool()), (0, Some(true)), "{o}");
        assert_eq!(o["expected_stock"], json!(110));
        // Valid near i64 boundary: MAX - 2 recorded, +1 outstanding = MAX-1 expected.
        let v = json!({"protocol_version":1,"product_id":"P1","recorded_stock":i64::MAX-2,"events":[ev(1,"init",i64::MAX,Value::Null,json!([]),Value::Null), ev(2,"order_deduction",1,json!("A"),json!([]),Value::Null), ev(3,"order_deduction",1,json!("A"),json!([]),Value::Null)],"applied_proposals":[],"proposal":{"credit_qty":1,"reverse_event_ids":[3]}});
        let (o, c) = run(&v);
        assert_eq!((c, o["ok"].as_bool()), (0, Some(true)), "{o}");
        assert_eq!(o["expected_stock"], json!(i64::MAX - 1));
        assert_eq!(o["ledger_actual"], json!(i64::MAX - 2));
    }
    #[test]
    fn subtotal_overflow_repeated_positive_quantities() {
        let mut v = base();
        v["events"].as_array_mut().unwrap().push(ev(
            5,
            "restock",
            i64::MAX,
            Value::Null,
            json!([]),
            Value::Null,
        ));
        v["events"].as_array_mut().unwrap().push(ev(
            6,
            "restock",
            1,
            Value::Null,
            json!([]),
            Value::Null,
        ));
        v["proposal"] = Value::Null;
        let (o, c) = run(&v);
        assert_eq!(c, 0);
        assert_eq!(o["errors"], json!(["overflow"]));
        assert_eq!(o["ok"], json!(false));
    }
    #[test]
    fn structs_encoded_as_json_arrays_rejected() {
        // Originals with proper objects must still pass.
        let (o, c) = run(&base());
        assert_eq!((c, o["ok"].as_bool()), (0, Some(true)), "{o}");
        let (o, c) = run(&with_repair());
        assert_eq!((c, o["ok"].as_bool()), (0, Some(true)), "{o}");
        // Root Request as array, in struct declaration order:
        // [protocol_version, product_id, recorded_stock, events, applied_proposals, proposal]
        let b = base();
        let root_array = json!([
            b["protocol_version"],
            b["product_id"],
            b["recorded_stock"],
            b["events"],
            b["applied_proposals"],
            b["proposal"]
        ]);
        assert_proto(&root_array, "decode_error");
        // Nested Event as array, in struct declaration order:
        // [id, product_id, kind, qty, order_id, reversed_ids, repair_proposal_id]
        let mut v = base();
        v["events"][1] = json!([2, "P1", "order_deduction", 10, "A", [], Value::Null]);
        assert_proto(&v, "decode_error");
        // Nested Applied as array, in struct declaration order:
        // [proposal_id, product_id, credit_qty, reverse_event_ids, status]
        let mut v = with_repair();
        v["applied_proposals"][0] = json!(["r1", "P1", 10, [3], "applied"]);
        assert_proto(&v, "decode_error");
        // Candidate Proposal as array, in struct declaration order:
        // [credit_qty, reverse_event_ids]
        let mut v = base();
        v["proposal"] = json!([10, [3]]);
        assert_proto(&v, "decode_error");
        // Wrong-type containers for the array/object fields are also rejected.
        let mut v = base();
        v["events"] = json!({});
        assert_proto(&v, "decode_error");
        let mut v = with_repair();
        v["applied_proposals"] = json!({});
        assert_proto(&v, "decode_error");
        let mut v = base();
        v["proposal"] = json!("proposal");
        assert_proto(&v, "decode_error");
    }
    #[test]
    fn duplicate_keys_still_rejected_after_preflight() {
        // The Value preflight collapses duplicate keys, so this proves the
        // typed decode from the original bytes still rejects them.
        let s = serde_json::to_string(&base()).unwrap();
        let dup_root = s.replacen(
            "\"product_id\":\"P1\"",
            "\"product_id\":\"P1\",\"product_id\":\"P1\"",
            1,
        );
        assert_ne!(dup_root, s);
        let (o, c) = run_raw(&dup_root);
        assert_eq!(c, 2, "{o}");
        assert_eq!(o["ok"], json!(false), "{o}");
        assert_eq!(o["errors"], json!(["decode_error"]), "{o}");
        assert_eq!(o["ledger_actual"], Value::Null);
        assert_eq!(o["expected_stock"], Value::Null);
        assert_eq!(o["outstanding_credit"], Value::Null);
        assert_eq!(o["proposal_matches"], Value::Null);
        // Duplicate key inside a nested event object is likewise rejected.
        let dup_event = s.replacen("\"qty\":10", "\"qty\":10,\"qty\":10", 1);
        assert_ne!(dup_event, s);
        let (o, c) = run_raw(&dup_event);
        assert_eq!(c, 2, "{o}");
        assert_eq!(o["errors"], json!(["decode_error"]), "{o}");
        assert_eq!(o["proposal_matches"], Value::Null);
        // The unmodified original still passes.
        let (o, c) = run_raw(&s);
        assert_eq!((c, o["ok"].as_bool()), (0, Some(true)), "{o}");
    }
}
