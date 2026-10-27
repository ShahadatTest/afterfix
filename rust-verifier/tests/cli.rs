use serde_json::{json, Value};
use std::io::Write;
use std::process::{Command, Stdio};

fn run(input: &[u8], args: &[&str]) -> (String, String, i32) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_afterfix-verifier"))
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn verifier");
    child.stdin.as_mut().unwrap().write_all(input).unwrap();
    let out = child.wait_with_output().unwrap();
    (
        String::from_utf8(out.stdout).unwrap(),
        String::from_utf8(out.stderr).unwrap(),
        out.status.code().unwrap_or(-1),
    )
}
fn run_json(v: &Value) -> (Value, String, String, i32) {
    let (o, e, c) = run(serde_json::to_string(v).unwrap().as_bytes(), &[]);
    assert_eq!(o.lines().count(), 1, "stdout must be one line: {o}");
    assert!(o.ends_with('\n'));
    assert!(o.len() < 16384);
    (serde_json::from_str(o.trim_end()).unwrap(), o, e, c)
}
fn ev(id: i64, kind: &str, qty: i64, order: Value, refs: Value, pid: Value) -> Value {
    json!({"id":id,"product_id":"P1","kind":kind,"qty":qty,"order_id":order,"reversed_ids":refs,"repair_proposal_id":pid})
}
fn req(events: Value, recorded: i64, applied: Value, proposal: Value) -> Value {
    json!({"protocol_version":1,"product_id":"P1","recorded_stock":recorded,"events":events,"applied_proposals":applied,"proposal":proposal})
}
fn dup_case() -> Value {
    req(
        json!([
            ev(1, "init", 100, Value::Null, json!([]), Value::Null),
            ev(2, "order_deduction", 10, json!("A"), json!([]), Value::Null),
            ev(
                10,
                "order_deduction",
                10,
                json!("A"),
                json!([]),
                Value::Null
            ),
            ev(11, "order_deduction", 4, json!("B"), json!([]), Value::Null),
            ev(12, "order_deduction", 4, json!("B"), json!([]), Value::Null),
            ev(13, "order_deduction", 4, json!("B"), json!([]), Value::Null),
            ev(20, "restock", 20, Value::Null, json!([]), Value::Null)
        ]),
        88,
        json!([]),
        json!({"credit_qty":18,"reverse_event_ids":[13,10,12]}),
    )
}
fn repaired_case() -> Value {
    let mut v = dup_case();
    v["events"].as_array_mut().unwrap().push(ev(
        21,
        "repair_credit",
        10,
        Value::Null,
        json!([10]),
        json!("r1"),
    ));
    v["recorded_stock"] = json!(98);
    v["applied_proposals"] = json!([{"proposal_id":"r1","product_id":"P1","credit_qty":10,"reverse_event_ids":[10],"status":"applied"}]);
    v["proposal"] = json!({"credit_qty":8,"reverse_event_ids":[12,13]});
    v
}
fn assert_domain(v: &Value, code: &str) {
    let (o, _, e, c) = run_json(v);
    assert_eq!((c, e.as_str()), (0, ""), "{o}");
    assert_eq!(o["ok"], json!(false), "{o}");
    assert_eq!(o["errors"], json!([code]), "{o}");
    assert_eq!(o["ledger_actual"], Value::Null);
    assert!(o.to_string().len() < 16384);
}
fn assert_proto(v: &Value, code: &str) {
    let (o, _, e, c) = run_json(v);
    assert_eq!((c, e.as_str()), (2, ""), "{o}");
    assert_eq!(o["ok"], json!(false), "{o}");
    assert_eq!(o["errors"], json!([code]), "{o}");
    assert_eq!(o["proposal_matches"], Value::Null);
}
#[test]
fn multiorder_multiple_extras_later_restock_sorted_refs() {
    let (o, _, e, c) = run_json(&dup_case());
    assert_eq!((c, e.as_str()), (0, ""));
    assert_eq!(o["ok"], json!(true));
    assert_eq!(o["expected_stock"], json!(106));
    assert_eq!(o["outstanding_credit"], json!(18));
    assert_eq!(o["reverse_event_ids"], json!([10, 12, 13]));
}
#[test]
fn null_candidate_with_outstanding_computed() {
    let mut v = dup_case();
    v["proposal"] = Value::Null;
    let (o, _, _, c) = run_json(&v);
    assert_eq!(c, 0);
    assert_eq!(o["ok"], json!(true));
    assert_eq!(o["ledger_actual"], json!(88));
    assert_eq!(o["expected_stock"], json!(106));
    assert_eq!(o["outstanding_credit"], json!(18));
    assert_eq!(o["reverse_event_ids"], json!([10, 12, 13]));
    assert_eq!(o["proposal_matches"], Value::Null);
}
#[test]
fn partial_prior_repair_then_candidate_for_remainder() {
    let (o, _, _, c) = run_json(&repaired_case());
    assert_eq!(c, 0);
    assert_eq!(o["ok"], json!(true));
    assert_eq!(o["expected_stock"], json!(106));
}
#[test]
fn bad_prior_refs_and_linkage_fail() {
    for (refs, credit, applied_credit) in [
        (json!([2]), 10, 10),
        (json!([99]), 10, 10),
        (json!([10, 10]), 20, 20),
        (json!([10]), 9, 9),
        (json!([10]), 10, 11),
    ] {
        let mut v = dup_case();
        v["events"].as_array_mut().unwrap().push(ev(
            21,
            "repair_credit",
            credit,
            Value::Null,
            refs.clone(),
            json!("r1"),
        ));
        v["recorded_stock"] = json!(88i64 + credit);
        v["applied_proposals"] = json!([{"proposal_id":"r1","product_id":"P1","credit_qty":applied_credit,"reverse_event_ids":refs,"status":"applied"}]);
        v["proposal"] = Value::Null;
        let (o, _, _, c) = run_json(&v);
        assert_eq!(c, 0, "{o}");
        assert_eq!(o["ok"], json!(false), "{o}");
    }
    let mut v = dup_case();
    v["events"].as_array_mut().unwrap().insert(
        2,
        ev(
            3,
            "repair_credit",
            10,
            Value::Null,
            json!([10]),
            json!("r1"),
        ),
    );
    v["recorded_stock"] = json!(98);
    v["applied_proposals"] = json!([{"proposal_id":"r1","product_id":"P1","credit_qty":10,"reverse_event_ids":[10],"status":"applied"}]);
    v["proposal"] = Value::Null;
    let (o, _, _, _) = run_json(&v);
    assert_eq!(o["ok"], json!(false));
    let mut v = dup_case();
    v["applied_proposals"] = json!([{"proposal_id":"unused","product_id":"P1","credit_qty":1,"reverse_event_ids":[10],"status":"applied"}]);
    let (o, _, _, _) = run_json(&v);
    assert_eq!(o["ok"], json!(false));
}
#[test]
fn reused_ref_across_repairs_fails() {
    let mut v = dup_case();
    let evs = v["events"].as_array_mut().unwrap();
    evs.push(ev(
        21,
        "repair_credit",
        10,
        Value::Null,
        json!([10]),
        json!("r1"),
    ));
    evs.push(ev(
        22,
        "repair_credit",
        10,
        Value::Null,
        json!([10]),
        json!("r2"),
    ));
    v["recorded_stock"] = json!(108);
    v["applied_proposals"] = json!([{"proposal_id":"r1","product_id":"P1","credit_qty":10,"reverse_event_ids":[10],"status":"applied"},{"proposal_id":"r2","product_id":"P1","credit_qty":10,"reverse_event_ids":[10],"status":"applied"}]);
    v["proposal"] = Value::Null;
    let (o, _, _, _) = run_json(&v);
    assert_eq!(o["ok"], json!(false));
}
#[test]
fn wrong_candidate_and_conflicting_qty_fail_exit0() {
    let mut v = dup_case();
    v["proposal"]["credit_qty"] = json!(17);
    let (o, _, _, c) = run_json(&v);
    assert_eq!((c, o["ok"].as_bool()), (0, Some(false)));
    assert_eq!(o["proposal_matches"], json!(false));
    let mut v = dup_case();
    v["events"][2]["qty"] = json!(11);
    v["recorded_stock"] = json!(87);
    let (o, _, _, c) = run_json(&v);
    assert_eq!((c, o["ok"].as_bool()), (0, Some(false)));
}
#[test]
fn malformed_unknown_duplicate_missing_trailing_oversize_exit2() {
    let cases: Vec<Vec<u8>> = vec![
        b"not json".to_vec(),
        b"{\"protocol_version\":1.0}".to_vec(),
        b"{\"protocol_version\":true}".to_vec(),
        serde_json::to_vec(&json!({"protocol_version":1,"product_id":"P1","recorded_stock":1,"events":[],"applied_proposals":[],"proposal":null,"zzz":1})).unwrap(),
        b"{\"protocol_version\":1,\"protocol_version\":1,\"product_id\":\"P1\",\"recorded_stock\":1,\"events\":[],\"applied_proposals\":[],\"proposal\":null}".to_vec(),
        serde_json::to_vec(&json!({"protocol_version":1,"product_id":"P1","recorded_stock":1,"events":[],"applied_proposals":[]})).unwrap(),
        b"{} {}".to_vec(),
        vec![b' '; 524_289],
    ];
    for input in cases {
        let (o, e, c) = run(&input, &[]);
        assert_eq!(c, 2, "{o}");
        assert_eq!(e, "");
        assert_eq!(o.lines().count(), 1);
        let v: Value = serde_json::from_str(o.trim_end()).unwrap();
        assert_eq!(v["ok"], json!(false));
        assert_eq!(v["proposal_matches"], Value::Null);
    }
}
#[test]
fn args_rejected_sanitized_and_empty_input() {
    let (o, e, c) = run(b"{}", &["--secret-value"]);
    assert_eq!(c, 2);
    assert_eq!(e, "");
    assert!(!o.contains("secret"));
    assert_eq!(o.lines().count(), 1);
    let (o, e, c) = run(b"", &[]);
    assert_eq!((c, e.as_str()), (2, ""));
    assert_eq!(o.lines().count(), 1);
}
#[test]
fn cross_product_and_string_rules() {
    let mut v = dup_case();
    v["events"][1]["product_id"] = json!("OTHER");
    let (o, _, _, c) = run_json(&v);
    assert_eq!((c, o["ok"].as_bool()), (0, Some(false)));
    let mut v = dup_case();
    v["product_id"] = json!("bad\u{0001}id");
    let (_, _, _, c) = run_json(&v);
    assert_eq!(c, 2);
}
#[test]
fn strict_kind_status_and_unknown_kind_exit2() {
    for bad in [
        json!({"init":null}),
        json!(["init"]),
        json!(1),
        json!(true),
        json!("bogus"),
    ] {
        let mut v = dup_case();
        v["events"][0]["kind"] = bad;
        assert_proto(&v, "decode_error");
    }
    for bad in [
        json!({"applied":null}),
        json!(["applied"]),
        json!(0),
        json!("pending"),
    ] {
        let mut v = repaired_case();
        v["applied_proposals"][0]["status"] = bad;
        assert_proto(&v, "decode_error");
    }
}
#[test]
fn numeric_types_and_i64_bounds_exit2() {
    for bad in [json!(true), json!(1.5), json!("10")] {
        let mut v = dup_case();
        v["events"][1]["qty"] = bad.clone();
        assert_proto(&v, "decode_error");
        let mut v = dup_case();
        v["recorded_stock"] = bad.clone();
        assert_proto(&v, "decode_error");
        let mut v = dup_case();
        v["events"][0]["id"] = bad.clone();
        assert_proto(&v, "decode_error");
        let mut v = dup_case();
        v["proposal"]["credit_qty"] = bad.clone();
        assert_proto(&v, "decode_error");
        let mut v = repaired_case();
        v["applied_proposals"][0]["credit_qty"] = bad;
        assert_proto(&v, "decode_error");
    }
    let s = serde_json::to_string(&dup_case()).unwrap();
    for raw in [
        s.replacen(
            "\"recorded_stock\":88",
            "\"recorded_stock\":9223372036854775808",
            1,
        ),
        s.replacen(
            "\"recorded_stock\":88",
            "\"recorded_stock\":-9223372036854775809",
            1,
        ),
        s.replacen("\"qty\":100", "\"qty\":9223372036854775808", 1),
    ] {
        let (o, e, c) = run(raw.as_bytes(), &[]);
        assert_eq!((c, e.as_str()), (2, ""));
        let v: Value = serde_json::from_str(o.trim_end()).unwrap();
        assert_eq!(v["errors"], json!(["decode_error"]));
        assert!(!o.contains("9223372036854775808"));
    }
}
#[test]
fn missing_nullable_fields_limits_ids_strings_exit2() {
    for field in [
        "order_id",
        "reversed_ids",
        "repair_proposal_id",
        "kind",
        "qty",
    ] {
        let mut v = dup_case();
        v["events"][0].as_object_mut().unwrap().remove(field);
        assert_proto(&v, "decode_error");
    }
    for field in ["status", "reverse_event_ids", "credit_qty", "proposal_id"] {
        let mut v = repaired_case();
        v["applied_proposals"][0]
            .as_object_mut()
            .unwrap()
            .remove(field);
        assert_proto(&v, "decode_error");
    }
    let mut v = dup_case();
    v["events"][1]["id"] = json!(1);
    assert_proto(&v, "event_order");
    let mut v = dup_case();
    v["events"][6]["id"] = json!(13);
    assert_proto(&v, "event_order");
    let mut v = dup_case();
    v["events"][0]["id"] = json!(0);
    assert_proto(&v, "invalid_id");
    let mut v = dup_case();
    v["events"][0]["id"] = json!(-3);
    assert_proto(&v, "invalid_id");
    let mut v = dup_case();
    v["proposal"]["reverse_event_ids"] = json!((1..=201).collect::<Vec<i64>>());
    assert_proto(&v, "limit_exceeded");
    let mut v = repaired_case();
    v["applied_proposals"][0]["reverse_event_ids"] = json!((1..=201).collect::<Vec<i64>>());
    assert_proto(&v, "limit_exceeded");
    let mut v = dup_case();
    v["events"][0]["reversed_ids"] = json!((1..=201).collect::<Vec<i64>>());
    assert_proto(&v, "limit_exceeded");
    let mut v = dup_case();
    v["events"] = json!((1..=201)
        .map(|i| ev(i, "init", 1, Value::Null, json!([]), Value::Null))
        .collect::<Vec<_>>());
    assert_proto(&v, "limit_exceeded");
    let mut v = dup_case();
    v["product_id"] = json!("a".repeat(129));
    assert_proto(&v, "invalid_string");
    let mut v = dup_case();
    v["events"][1]["order_id"] = json!("a".repeat(129));
    assert_proto(&v, "invalid_string");
}
#[test]
fn applied_and_repair_domain_failures_exact_errors() {
    let mut v = repaired_case();
    v["applied_proposals"][0]["product_id"] = json!("OTHER");
    assert_domain(&v, "repair_linkage");
    let mut v = repaired_case();
    let d = v["applied_proposals"][0].clone();
    v["applied_proposals"].as_array_mut().unwrap().push(d);
    assert_domain(&v, "repair_linkage");
    let mut v = repaired_case();
    v["applied_proposals"] = json!([]);
    v["proposal"] = Value::Null;
    assert_domain(&v, "repair_linkage");
    let mut v = repaired_case();
    v["applied_proposals"][0]["reverse_event_ids"] = json!([12]);
    assert_domain(&v, "repair_linkage");
    let mut v = repaired_case();
    v["applied_proposals"][0]["reverse_event_ids"] = json!([10, 10]);
    assert_domain(&v, "repair_linkage");
    let mut v = repaired_case();
    v["applied_proposals"][0]["credit_qty"] = json!(11);
    assert_domain(&v, "repair_linkage");
    // Duplicate repair proposal id: add a second repair event reusing r1.
    let mut v = repaired_case();
    v["events"].as_array_mut().unwrap().push(ev(
        22,
        "repair_credit",
        4,
        Value::Null,
        json!([12]),
        json!("r1"),
    ));
    v["recorded_stock"] = json!(102);
    v["proposal"] = Value::Null;
    assert_domain(&v, "repair_linkage");
    // Repair-event credit mismatching its refs, with applied agreeing with the (wrong) event credit.
    let mut v = repaired_case();
    v["events"][7]["qty"] = json!(9);
    v["applied_proposals"][0]["credit_qty"] = json!(9);
    v["recorded_stock"] = json!(97);
    assert_domain(&v, "repair_credit_mismatch");
}
#[test]
fn candidate_ref_failures_exact_errors() {
    for refs in [
        json!([10, 12, 12]),
        json!([10, 12]),
        json!([10, 12, 13, 11]),
        json!([10, 12, 99]),
    ] {
        let mut v = dup_case();
        v["proposal"]["reverse_event_ids"] = refs;
        assert_domain(&v, "candidate_mismatch");
        let (o, _, _, _) = run_json(&v);
        assert_eq!(o["proposal_matches"], json!(false));
    }
    let mut v = dup_case();
    v["proposal"]["credit_qty"] = json!(0);
    v["proposal"]["reverse_event_ids"] = json!([]);
    assert_domain(&v, "candidate_mismatch");
}
#[test]
fn multiple_init_overflow_and_near_boundary() {
    let v = req(
        json!([
            ev(1, "init", 60, Value::Null, json!([]), Value::Null),
            ev(2, "init", 40, Value::Null, json!([]), Value::Null),
            ev(3, "order_deduction", 10, json!("A"), json!([]), Value::Null),
            ev(4, "order_deduction", 10, json!("A"), json!([]), Value::Null),
            ev(5, "restock", 20, Value::Null, json!([]), Value::Null)
        ]),
        100,
        json!([]),
        json!({"credit_qty":10,"reverse_event_ids":[4]}),
    );
    let (o, _, _, c) = run_json(&v);
    assert_eq!((c, o["ok"].as_bool()), (0, Some(true)), "{o}");
    assert_eq!(o["ledger_actual"], json!(100));
    // Repeated positive restock quantities overflow the restock subtotal.
    let mut v = dup_case();
    v["events"].as_array_mut().unwrap().push(ev(
        21,
        "restock",
        i64::MAX,
        Value::Null,
        json!([]),
        Value::Null,
    ));
    v["events"].as_array_mut().unwrap().push(ev(
        22,
        "restock",
        1,
        Value::Null,
        json!([]),
        Value::Null,
    ));
    v["proposal"] = Value::Null;
    assert_domain(&v, "overflow");
    // Valid near-boundary ledger.
    let v = req(
        json!([
            ev(1, "init", i64::MAX, Value::Null, json!([]), Value::Null),
            ev(2, "order_deduction", 1, json!("A"), json!([]), Value::Null),
            ev(3, "order_deduction", 1, json!("A"), json!([]), Value::Null)
        ]),
        i64::MAX - 2,
        json!([]),
        json!({"credit_qty":1,"reverse_event_ids":[3]}),
    );
    let (o, _, _, c) = run_json(&v);
    assert_eq!((c, o["ok"].as_bool()), (0, Some(true)), "{o}");
    assert_eq!(o["expected_stock"], json!(i64::MAX - 1));
}
