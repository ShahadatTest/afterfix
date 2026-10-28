import importlib.util
import json
import socket
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("audit_dependency_inventory", ROOT / "tools" / "audit_dependency_inventory.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("real network attempted")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket.socket, "connect_ex", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)


def write_inventory(tmp_path, packages, **overrides):
    doc = {
        "schema_version": 1,
        "generated_at_utc": "2026-10-28T00:00:00+00:00",
        "coverage": "test coverage",
        "source_hashes": {"requirements.txt": "a" * 64},
        "packages": packages,
    }
    doc.update(overrides)
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def pkg(eco="PyPI", name="requests", version="2.32.0"):
    return {"ecosystem": eco, "name": name, "version": version}


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, body, headers, timeout))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def body_for(results):
    return json.dumps({"results": results}).encode()


def assert_invalid_before_transport(tmp_path, packages, **overrides):
    inv = write_inventory(tmp_path, packages, **overrides)
    fake = FakeTransport([])
    out = tmp_path / "out"
    with pytest.raises(mod.AuditError):
        mod.run_audit(inv, out, query_osv=True, transport=fake)
    assert fake.calls == []
    assert not out.exists()


def test_default_no_transport_and_not_queried(tmp_path):
    inv = write_inventory(tmp_path, [pkg(), pkg("npm", "@scope/name", "1.0.0"), pkg("crates.io", "serde", "1.0.0")])
    fake = FakeTransport([])
    out = tmp_path / "out"
    rc = mod.run_audit(inv, out, query_osv=False, transport=fake)
    assert rc == 0
    assert fake.calls == []
    summary = json.loads((out / "summary.json").read_text())
    assert summary["status"] == "not_queried"
    assert summary["queried_count"] == 0
    assert summary["advisory_instance_count"] is None
    req = json.loads((out / "request.json").read_text())
    # Exact verified triples in exact deterministic (sorted) order.
    assert req == {"queries": [
        {"package": {"ecosystem": "PyPI", "name": "requests"}, "version": "2.32.0"},
        {"package": {"ecosystem": "crates.io", "name": "serde"}, "version": "1.0.0"},
        {"package": {"ecosystem": "npm", "name": "@scope/name"}, "version": "1.0.0"},
    ]}
    assert "source_hashes" not in json.dumps(req)


def test_dedup_and_query_mapping_findings(tmp_path):
    pkgs = [pkg(), pkg(), pkg("npm", "@scope/name", "1.0.0")]
    inv = write_inventory(tmp_path, pkgs)
    fake = FakeTransport([(200, body_for([{"vulns": [{"id": "GHSA-aaaa-bbbb-cccc", "modified": "2026-01-01T00:00:00Z"}]}, {}]))])
    out = tmp_path / "out"
    rc = mod.run_audit(inv, out, query_osv=True, transport=fake)
    assert rc == 1
    assert len(fake.calls) == 1
    url, body, headers, timeout = fake.calls[0]
    assert url == mod.ENDPOINT
    assert "Authorization" not in headers and set(headers) == {"Content-Type", "User-Agent"}
    sent = json.loads(body)
    assert len(sent["queries"]) == 2  # duplicate removed
    assert "a" * 64 not in body.decode()  # provenance never transmitted
    summary = json.loads((out / "summary.json").read_text())
    assert summary["status"] == "findings"
    assert summary["advisory_instance_count"] == 1 and summary["advisory_unique_count"] == 1


def test_complete_no_known_advisories_rc0(tmp_path):
    inv = write_inventory(tmp_path, [pkg()])
    fake = FakeTransport([(200, body_for([{}]))])
    out = tmp_path / "out"
    assert mod.run_audit(inv, out, query_osv=True, transport=fake) == 0
    assert json.loads((out / "summary.json").read_text())["status"] == "complete_no_known_advisories"


def test_two_batches_103_ordered(tmp_path):
    pkgs = [pkg(name=f"pkg{i:03d}", version="1.0.0") for i in range(103)]
    inv = write_inventory(tmp_path, pkgs)
    calls_sizes = []

    def transport(url, body, headers, timeout):
        qs = json.loads(body)["queries"]
        calls_sizes.append(len(qs))
        return 200, body_for([{} for _ in qs])

    out = tmp_path / "out"
    assert mod.run_audit(inv, out, query_osv=True, transport=transport) == 0
    assert calls_sizes == [100, 3]
    results = json.loads((out / "results.json").read_text())
    assert [p["name"] for p in results["packages"]] == sorted(p["name"] for p in results["packages"])
    assert all(p["queried"] for p in results["packages"])


def test_real_103_inventory_default_prepare_not_queried(tmp_path):
    inv = ROOT / "docs" / "evaluations" / "dependency-inventory-2026-10-28.json"
    fake = FakeTransport([])
    out = tmp_path / "out"
    assert mod.run_audit(inv, out, query_osv=False, transport=fake) == 0
    assert fake.calls == []
    summary = json.loads((out / "summary.json").read_text())
    assert summary["status"] == "not_queried" and summary["total_count"] == 103
    req = json.loads((out / "request.json").read_text())
    assert len(req["queries"]) == 103
    triples = [(q["package"]["ecosystem"], q["package"]["name"], q["version"]) for q in req["queries"]]
    assert triples == sorted(triples)
    assert ("npm", "@scope/name" if False else "@oxc-project/types", "0.155.0") in triples
    assert ("PyPI", "a" if False else "Pygments", "2.21.0") in triples


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(extra="x"),
    lambda d: d.update(schema_version=2),
    lambda d: d["packages"][0].update(token="secret-like"),
    lambda d: d["packages"][0].update(ecosystem="Maven"),
    lambda d: d["packages"][0].update(name="https://evil.example/x"),
    lambda d: d["packages"][0].update(name="bad name"),
    lambda d: d["packages"][0].update(version="1.0:bad"),
    lambda d: d["packages"][0].update(version=""),
    lambda d: d.update(packages=[]),
])
def test_invalid_inventory_before_transport_and_output(tmp_path, mutate):
    doc = {"schema_version": 1, "generated_at_utc": "2026-10-28T00:00:00+00:00", "coverage": "c",
           "source_hashes": {"requirements.txt": "a" * 64}, "packages": [pkg()]}
    mutate(doc)
    inv = tmp_path / "inv.json"
    inv.write_text(json.dumps(doc))
    fake = FakeTransport([])
    out = tmp_path / "out"
    with pytest.raises(mod.AuditError):
        mod.run_audit(inv, out, query_osv=True, transport=fake)
    assert fake.calls == []
    assert not out.exists()


def test_oversize_inventory_rejected(tmp_path):
    inv = tmp_path / "inv.json"
    inv.write_bytes(b" " * (mod.MAX_INVENTORY_BYTES + 1))
    out = tmp_path / "out"
    with pytest.raises(mod.AuditError):
        mod.run_audit(inv, out, query_osv=True, transport=FakeTransport([]))
    assert not out.exists()


@pytest.mark.parametrize("bad", ["$notpublic", ";notpublic", "not public", "not\npublic", "-lead", "trail-", ".lead", "a..b ", "éclair", "a/b", "a\\b", ""])
def test_invalid_pypi_names_rejected_before_transport(tmp_path, bad):
    assert_invalid_before_transport(tmp_path, [pkg(name=bad)])


@pytest.mark.parametrize("good,version", [("a", "1"), ("Z", "0.0.1"), ("a-b_c.d", "1.0a1"), ("requests", "2.32.0")])
def test_valid_pypi_names_including_one_char(tmp_path, good, version):
    inv = write_inventory(tmp_path, [pkg(name=good, version=version)])
    out = tmp_path / "out"
    assert mod.run_audit(inv, out, query_osv=False, transport=FakeTransport([])) == 0


@pytest.mark.parametrize("bad", [
    "@scope", "@scope/", "@/name", "@scope/name/extra", "@scope//name",
    "@@scope/name", "@scope/@name", "scope/name", "@../name", "@scope/..",
    "@.scope/name", "@scope/.name", "@sco pe/name", "@scope/na me",
    "@scope/name\n", "@scope/name ", "@scope/name?x", "https://x/y",
    "@scope\\name", "na/me", "@scope/name#x", "..", ".hidden",
])
def test_invalid_npm_names_rejected_before_transport(tmp_path, bad):
    assert_invalid_before_transport(tmp_path, [pkg("npm", bad, "1.0.0")])


@pytest.mark.parametrize("good", ["@scope/name", "@oxc-project/types", "@a/b", "react", "lightningcss-linux-x64-gnu", "fsevents"])
def test_valid_npm_names(tmp_path, good):
    inv = write_inventory(tmp_path, [pkg("npm", good, "1.0.0-beta.1")])
    out = tmp_path / "out"
    assert mod.run_audit(inv, out, query_osv=False, transport=FakeTransport([])) == 0


@pytest.mark.parametrize("eco,name,version", [
    ("crates.io", "serde", "1.0.229"),
    ("crates.io", "a", "0.1.0"),
    ("crates.io", "proc-macro2", "1.0.107+build"),
    ("npm", "vite", "8.3.5"),
    ("PyPI", "certifi", "2026.10.23"),
])
def test_valid_three_ecosystems(tmp_path, eco, name, version):
    inv = write_inventory(tmp_path, [pkg(eco, name, version)])
    out = tmp_path / "out"
    assert mod.run_audit(inv, out, query_osv=False, transport=FakeTransport([])) == 0


@pytest.mark.parametrize("overrides", [
    {"schema_version": 1.0},
    {"schema_version": True},
    {"schema_version": "1"},
    {"source_hashes": {"requirements.txt": "a" * 64 + "\n"}},
    {"source_hashes": {"requirements.txt": "a" * 63}},
    {"source_hashes": {"requirements.txt": "A" * 64}},
    {"source_hashes": {"requirements.txt": "g" * 64}},
])
def test_invalid_schema_and_hash_before_transport(tmp_path, overrides):
    assert_invalid_before_transport(tmp_path, [pkg()], **overrides)


@pytest.mark.parametrize("payload,reason", [
    ({"nope": []}, "invalid_response"),
    ({"results": []}, "invalid_response"),
    ({"results": [{}, {}]}, "invalid_response"),
    ({"results": [{}], "error": "x"}, "invalid_response"),
    ({"results": [{"vulns": [{"id": "bad id!", "modified": "2026-01-01"}]}]}, "invalid_response"),
    ({"results": [{"vulns": [{"id": "GHSA-aaaa-bbbb-cccc", "modified": 5}]}]}, "invalid_response"),
    ({"results": [{"vulns": [{"id": "GHSA-aaaa-bbbb-cccc\n", "modified": "2026-01-01T00:00:00Z"}]}]}, "invalid_response"),
    ({"results": [{"vulns": [{"id": "GHSA-aaaa-bbbb-cccc", "modified": "2026-01-01T00:00:00Z\n"}]}]}, "invalid_response"),
    ({"results": [{}], "next_page_token": "tok"}, "pagination"),
    ({"results": [{"next_page_token": "tok"}]}, "pagination"),
    ({"results": [{"vulns": [], "next_page_token": "tok"}]}, "pagination"),
    ({"results": [{"next_page_token": 123}]}, "invalid_response"),
    ({"results": [{"next_page_token": ""}]}, "invalid_response"),
    ({"results": [{"next_page_token": None}]}, "invalid_response"),
    ({"results": [{}], "next_page_token": 5}, "invalid_response"),
])
def test_malformed_responses_fail_closed(tmp_path, payload, reason):
    inv = write_inventory(tmp_path, [pkg()])
    fake = FakeTransport([(200, json.dumps(payload).encode())])
    out = tmp_path / "out"
    rc = mod.run_audit(inv, out, query_osv=True, transport=fake)
    assert rc == 2
    summary = json.loads((out / "summary.json").read_text())
    assert summary["status"] == "incomplete" and summary["reason"] == reason
    assert summary["queried_count"] == 0


@pytest.mark.parametrize("raw", [
    b'{"results":[{"vulns":[{"id":"GHSA-aaaa-bbbb-cccc","modified":"2026-01-01T00:00:00Z"}]}],"results":[{}]}',
    b'{"results":[{}],"results":[{"vulns":[{"id":"GHSA-aaaa-bbbb-cccc","modified":"2026-01-01T00:00:00Z"}]}]}',
    b'{"results":[{"vulns":[{"id":"GHSA-aaaa-bbbb-cccc","modified":"2026-01-01T00:00:00Z"}],"vulns":[]}]}',
    b'{"results":[{"vulns":[{"id":"GHSA-aaaa-bbbb-cccc","id":"CVE-2026-0001","modified":"2026-01-01T00:00:00Z"}]}]}',
])
def test_duplicate_response_keys_cannot_hide_findings(tmp_path, raw):
    inv = write_inventory(tmp_path, [pkg()])
    fake = FakeTransport([(200, raw)])
    out = tmp_path / "out"
    assert mod.run_audit(inv, out, query_osv=True, transport=fake) == 2
    summary = json.loads((out / "summary.json").read_text())
    assert summary["status"] == "incomplete" and summary["reason"] == "invalid_response"
    assert summary["advisory_instance_count"] is None


def test_item_pagination_preserves_earlier_batch(tmp_path):
    pkgs = [pkg(name=f"pkg{i:03d}", version="1.0.0") for i in range(103)]
    inv = write_inventory(tmp_path, pkgs)
    first = body_for([{}] * 100)
    second = body_for([{"next_page_token": "tok"}, {}, {}])
    fake = FakeTransport([(200, first), (200, second)])
    out = tmp_path / "out"
    assert mod.run_audit(inv, out, query_osv=True, transport=fake) == 2
    summary = json.loads((out / "summary.json").read_text())
    assert summary["status"] == "incomplete" and summary["reason"] == "pagination"
    assert summary["queried_count"] == 100 and summary["counts_partial"] is True


def test_http_error_and_oversize_fail_closed(tmp_path):
    inv = write_inventory(tmp_path, [pkg()])
    out = tmp_path / "o1"
    rc = mod.run_audit(inv, out, query_osv=True, transport=FakeTransport([(500, b"secret body")]))
    assert rc == 2
    assert "secret" not in (out / "summary.json").read_text()
    out2 = tmp_path / "o2"
    rc = mod.run_audit(inv, out2, query_osv=True, transport=FakeTransport([(200, b"x" * (mod.MAX_RESPONSE_BYTES + 1))]))
    assert rc == 2
    assert json.loads((out2 / "summary.json").read_text())["reason"] == "response_too_large"


def test_second_batch_failure_preserves_first_no_retry(tmp_path):
    pkgs = [pkg(name=f"pkg{i:03d}", version="1.0.0") for i in range(103)]
    inv = write_inventory(tmp_path, pkgs)
    first = body_for([{"vulns": [{"id": "CVE-2026-0001", "modified": "2026-01-01T00:00:00Z"}]}] + [{}] * 99)
    fake = FakeTransport([(200, first), (503, b"err")])
    out = tmp_path / "out"
    rc = mod.run_audit(inv, out, query_osv=True, transport=fake)
    assert rc == 2 and len(fake.calls) == 2  # no retry
    summary = json.loads((out / "summary.json").read_text())
    assert summary["status"] == "incomplete" and summary["queried_count"] == 100
    assert summary["counts_partial"] is True and summary["advisory_instance_count"] == 1
    results = json.loads((out / "results.json").read_text())
    assert sum(1 for p in results["packages"] if p["queried"]) == 100


def test_duplicate_advisory_ids_unique_vs_instances(tmp_path):
    inv = write_inventory(tmp_path, [pkg(), pkg(name="other")])
    vuln = {"id": "GHSA-aaaa-bbbb-cccc", "modified": "2026-01-01T00:00:00Z"}
    fake = FakeTransport([(200, body_for([{"vulns": [vuln]}, {"vulns": [vuln]}]))])
    out = tmp_path / "out"
    assert mod.run_audit(inv, out, query_osv=True, transport=fake) == 1
    s = json.loads((out / "summary.json").read_text())
    assert s["advisory_instance_count"] == 2 and s["advisory_unique_count"] == 1


def test_existing_output_refused_unchanged(tmp_path):
    inv = write_inventory(tmp_path, [pkg()])
    out = tmp_path / "out"
    out.mkdir()
    sentinel = out / "keep.txt"
    sentinel.write_text("keep")
    with pytest.raises(mod.AuditError):
        mod.run_audit(inv, out, query_osv=True, transport=FakeTransport([]))
    assert sentinel.read_text() == "keep"
    assert not (out / "summary.json").exists()


def test_cli_default_sanitized_error(tmp_path, capsys):
    rc = mod.main(["--inventory", str(tmp_path / "missing.json"), "--output", str(tmp_path / "out")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err and str(tmp_path) not in err


class _RecordingHTTPError(urllib.error.HTTPError):
    def __init__(self):
        super().__init__(mod.ENDPOINT, 500, "err", hdrs={}, fp=None)
        self.read_calls = []

    def read(self, *a, **k):
        self.read_calls.append((a, k))
        raise AssertionError("unbounded/forbidden HTTPError.read called")


def test_real_transport_http_error_never_reads_body(monkeypatch):
    err = _RecordingHTTPError()
    handlers_seen = []

    class Opener:
        def open(self, request, timeout=None):
            raise err

    def fake_build_opener(*handlers):
        handlers_seen.extend(handlers)
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)
    status, data = mod._real_transport(mod.ENDPOINT, b"{}", {"Content-Type": "application/json"}, 5.0)
    assert (status, data) == (500, b"")
    assert err.read_calls == []
    proxy = [h for h in handlers_seen if isinstance(h, urllib.request.ProxyHandler)]
    assert len(proxy) == 1 and proxy[0].proxies == {}
    assert any(isinstance(h, mod._NoRedirect) for h in handlers_seen)


def test_real_transport_bounded_read_and_headers(monkeypatch):
    seen = {}

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            seen["read_arg"] = n
            return b'{"results":[{}]}'

    class Opener:
        def open(self, request, timeout=None):
            seen["url"] = request.full_url
            seen["timeout"] = timeout
            seen["headers"] = dict(request.header_items())
            return Resp()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *h: Opener())
    status, data = mod._real_transport(mod.ENDPOINT, b"{}", {"Content-Type": "application/json", "User-Agent": mod.USER_AGENT}, 7.0)
    assert status == 200 and data == b'{"results":[{}]}'
    assert seen["read_arg"] == mod.MAX_RESPONSE_BYTES + 1
    assert seen["url"] == mod.ENDPOINT and seen["timeout"] == 7.0
    assert "Authorization" not in seen["headers"]
