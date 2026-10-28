"""Offline MetaClient tests: injected transport or mocked HTTP only."""

import json
import ssl
import urllib.error
import urllib.request

import pytest

from app.meta_client import MODEL, MetaClient, MetaClientError

DUMMY = "dummy-test-credential-not-real"


def ok(i=100, o=50):
    return {"status": "completed", "output": [], "usage": {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}}


class FakeTransport:
    def __init__(self, handler=None):
        self.handler = handler or (lambda payload: ok())
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(json.loads(json.dumps(payload)))
        return self.handler(payload)


def client(handler=None):
    fake = FakeTransport(handler)
    return MetaClient(transport=fake), fake


TOOLS = [{"type": "function", "name": "read_events", "description": "read", "strict": True, "parameters": {"type": "object", "properties": {"event_ids": {"type": "array", "items": {"type": "integer"}}}}}]
FORMAT = {"type": "json_schema", "name": "result", "strict": True, "schema": {"type": "object", "properties": {}}}


def test_payload_contract_with_tools_and_format():
    c, fake = client()
    c.create_response([{"role": "user", "content": "hello"}], tools=TOOLS, text_format=FORMAT, max_output_tokens=16)
    payload = fake.payloads[0]
    assert payload["model"] == MODEL == "muse-spark-1.3"
    assert payload["store"] is False
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["max_output_tokens"] == 16
    assert payload["tool_choice"] == "auto"
    assert payload["parallel_tool_calls"] is False
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    assert DUMMY not in json.dumps(payload)


def test_payload_without_tools_has_no_tool_controls_and_max_bound():
    c, fake = client()
    c.create_response("hello", max_output_tokens=2048)
    payload = fake.payloads[0]
    assert "tools" not in payload and "tool_choice" not in payload and "parallel_tool_calls" not in payload
    assert payload["max_output_tokens"] == 2048


def test_default_transport_http_mocked_and_credential_only_in_header(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", DUMMY)
    captured = {}

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return json.dumps(ok()).encode()

    def fake_urlopen(req, timeout=None, context=None):
        captured["req"] = req
        captured["timeout"] = timeout
        captured["context"] = context
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    c = MetaClient()
    assert c.create_response("hello")["status"] == "completed"
    context = captured["context"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.get_ca_certs()
    req = captured["req"]
    sent = json.loads(req.data.decode())
    assert sent["model"] == "muse-spark-1.3" and sent["store"] is False
    assert DUMMY not in req.data.decode()
    assert req.get_header("Authorization") == f"Bearer {DUMMY}"


@pytest.mark.parametrize("bad_model", ["muse-spark-1.0", "muse-wonder-1.0", "gpt-example", "muse-spark-1.3-contributor", ""])
def test_other_models_and_contributor_mode_rejected(bad_model):
    with pytest.raises((ValueError, TypeError)):
        MetaClient(transport=FakeTransport(), model=bad_model)
    c, fake = client()
    with pytest.raises(TypeError):
        c.create_response("hello", contributor=True)  # no contributor/training mode API
    assert fake.payloads == []


@pytest.mark.parametrize("tools", [[{"type": "web_search"}], [{"type": "computer_use"}], [{"type": "function", "name": "x", "parameters": {"type": "object", "properties": {}}}, {"type": "hosted_tool"}]])
def test_remote_or_hosted_tools_rejected_before_transport(tools):
    c, fake = client()
    with pytest.raises(ValueError):
        c.create_response("hello", tools=tools)
    assert fake.payloads == []


@pytest.mark.parametrize("items", [
    [{"type": "input_image", "image_url": "https://example.invalid/x.png"}],
    [{"role": "user", "content": [{"type": "image_url", "image_url": "x"}]}],
    [{"role": "user", "content": [{"type": "input_file", "file_id": "f1"}]}],
    [{"role": "user", "content": "x", "previous_response_id": "resp_1"}],
])
def test_remote_media_and_chained_responses_rejected_before_transport(items):
    c, fake = client()
    with pytest.raises(ValueError):
        c.create_response(items)
    assert fake.payloads == []


@pytest.mark.parametrize("url", [
    "http://api.meta.ai/v1/responses",
    "https://example.com/v1/responses",
    "https://user:pass@api.meta.ai/v1/responses",
    "https://api.meta.ai/v1/responses?x=1",
    "https://api.meta.ai/v1/responses#frag",
    "https://api.meta.ai/v2/responses",
    "https://api.meta.ai:443/v1/responses",
    "https://api.meta.ai/v1/responses/extra",
    "ftp://api.meta.ai/v1/responses",
    "not a url",
])
def test_invalid_endpoints_rejected(monkeypatch, url):
    monkeypatch.setenv("MODEL_API_KEY", DUMMY)
    with pytest.raises(ValueError):
        MetaClient(base_url=url)


@pytest.mark.parametrize("limit", [0, 15, 2049, 10000, True, 16.5, "16"])
def test_invalid_output_limits_before_transport(limit):
    c, fake = client()
    with pytest.raises(ValueError):
        c.create_response("hello", max_output_tokens=limit)
    assert fake.payloads == []


def test_invalid_text_format_before_transport():
    c, fake = client()
    for fmt in [{"type": "json_schema", "strict": False, "schema": {}}, {"type": "json_object"}, {"type": "json_schema", "strict": True}]:
        with pytest.raises(ValueError):
            c.create_response("hello", text_format=fmt)
    assert fake.payloads == []


def test_request_limit_is_before_seventh_transport_call():
    c, fake = client(lambda p: ok(1, 1))
    for _ in range(6):
        c.create_response("hi", max_output_tokens=16)
    with pytest.raises(MetaClientError):
        c.create_response("hi", max_output_tokens=16)
    assert len(fake.payloads) == 6
    assert c.usage_summary()["requests"] == 6


def test_conservative_cost_limit_before_transport():
    c, fake = client()
    with pytest.raises(MetaClientError):
        c.create_response("x" * 190000, max_output_tokens=2048)
    assert fake.payloads == []
    summary = c.usage_summary()
    assert summary["requests"] == 0


@pytest.mark.parametrize("exc", [TypeError("bad type SECRET"), RuntimeError("boom SECRET"), urllib.error.HTTPError("https://api.meta.ai/v1/responses", 500, "SECRET http", {}, None), urllib.error.URLError("SECRET url")])
def test_transport_failures_exactly_once_retained_reservation_and_block(exc):
    secret = "SECRET"

    def handler(payload):
        raise exc

    c, fake = client(handler)
    with pytest.raises(MetaClientError) as caught:
        c.create_response("hello", max_output_tokens=16)
    assert secret not in str(caught.value)
    assert len(fake.payloads) == 1
    summary = c.usage_summary()
    assert summary["unknown_usage_calls"] == 1
    assert summary["blocked"] is True
    assert summary["reserved_cost"] > 0
    assert summary["estimated_cost"] >= summary["reserved_cost"] > 0
    with pytest.raises(MetaClientError):
        c.create_response("hello", max_output_tokens=16)
    assert len(fake.payloads) == 1  # blocked call does not retry transport


def test_valid_usage_reconciles_cost_including_all_output_tokens():
    c, _ = client(lambda p: ok(100, 50))  # output_tokens includes reasoning tokens per API usage
    c.create_response("hello", max_output_tokens=100)
    summary = c.usage_summary()
    assert summary["input_tokens"] == 100
    assert summary["output_tokens"] == 50
    assert summary["total_tokens"] == 150
    assert summary["estimated_cost"] == pytest.approx(100 * 1.25e-6 + 50 * 4.25e-6)
    assert summary["reserved_cost"] == 0
    assert summary["unknown_usage_calls"] == 0
    assert summary["blocked"] is False


@pytest.mark.parametrize("resp", [
    {"status": "completed", "output": []},
    {"status": "completed", "output": [], "usage": {"input_tokens": -1, "output_tokens": 0, "total_tokens": 0}},
    {"status": "completed", "output": [], "usage": {"input_tokens": 1, "output_tokens": True, "total_tokens": 2}},
    {"status": "completed", "output": [], "usage": {"input_tokens": 1, "total_tokens": 1}},
    {"status": "completed", "output": [], "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 5}},
    {"status": "completed", "output": [], "usage": {"input_tokens": 1.5, "output_tokens": 1, "total_tokens": 3}},
    {"error": {"message": "SECRET upstream detail"}, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}},
])
def test_bad_usage_cannot_refund_and_blocks_future_calls(resp):
    c, fake = client(lambda p: resp)
    with pytest.raises(MetaClientError) as caught:
        c.create_response("hello", max_output_tokens=16)
    assert "SECRET" not in str(caught.value)
    assert len(fake.payloads) == 1
    summary = c.usage_summary()
    assert summary["unknown_usage_calls"] == 1
    assert summary["blocked"] is True
    assert summary["reserved_cost"] > 0
    assert summary["estimated_cost"] > 0
    with pytest.raises(MetaClientError):
        c.create_response("hello", max_output_tokens=16)
    assert len(fake.payloads) == 1
