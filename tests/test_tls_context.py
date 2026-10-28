"""Offline tests for the shared verified TLS context and integrations."""
import json
import shutil
import ssl
import sys
import types
import urllib.request

import pytest

import app.meta_client as meta_client_module
import app.runtime_budget as runtime_budget_module
from app.meta_client import MetaClient, MetaClientError
from app.runtime_budget import _NoRedirect
from app.tls_context import trusted_tls_context

MESSAGE = "TLS trust configuration unavailable"
DUMMY = "dummy-test-credential-not-real"
SENTINEL = "PRIVATE_SYNTHETIC_PATH_SENTINEL"


def _assert_verified(context):
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.get_ca_certs()
    assert not context.keylog_filename


def _assert_sanitized(caught):
    assert str(caught.value) == MESSAGE
    assert SENTINEL not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def _fake_certifi(monkeypatch, version, cafile):
    fake = types.ModuleType("certifi")
    fake.__version__ = version
    fake.where = lambda: cafile
    monkeypatch.setitem(sys.modules, "certifi", fake)
    return fake


def _fake_certifi_where(monkeypatch, version, where):
    fake = types.ModuleType("certifi")
    fake.__version__ = version
    fake.where = where
    monkeypatch.setitem(sys.modules, "certifi", fake)
    return fake


def test_real_context_is_verified_with_nonempty_cas():
    _assert_verified(trusted_tls_context())


def test_environment_sentinels_are_ignored_and_create_no_files(monkeypatch, tmp_path):
    sentinels = {}
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE"):
        path = tmp_path / (name.lower() + ".sentinel")
        sentinels[name] = path
        monkeypatch.setenv(name, str(path))
    _assert_verified(trusted_tls_context())
    assert all(not path.exists() for path in sentinels.values())


def test_missing_certifi_is_sanitized(monkeypatch):
    monkeypatch.setitem(sys.modules, "certifi", None)
    with pytest.raises(RuntimeError) as caught:
        trusted_tls_context()
    assert str(caught.value) == MESSAGE
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


@pytest.mark.parametrize("version", [
    "2026.10.18", "2025.01.01", "2026.10.19\n", "2026.10.19.extra",
    "2026.02.30", "2026.13.01", "2026.1.19", "", "not-a-version",
    None, 20261019, b"2026.10.23",
])
def test_old_or_malformed_certifi_version_is_sanitized(monkeypatch, version):
    import certifi
    _fake_certifi(monkeypatch, version, certifi.where())
    with pytest.raises(RuntimeError) as caught:
        trusted_tls_context()
    assert str(caught.value) == MESSAGE


def test_minimum_and_installed_versions_accepted(monkeypatch):
    import certifi
    _fake_certifi(monkeypatch, "2026.10.19", certifi.where())
    _assert_verified(trusted_tls_context())


@pytest.mark.parametrize("kind", ["missing", "empty", "broken"])
def test_missing_or_broken_ca_file_is_sanitized(monkeypatch, tmp_path, kind):
    path = tmp_path / "ca.pem"
    if kind == "empty":
        path.write_text("", encoding="ascii")
    elif kind == "broken":
        path.write_text("not a certificate", encoding="ascii")
    _fake_certifi(monkeypatch, "2026.10.23", str(path))
    with pytest.raises(RuntimeError) as caught:
        trusted_tls_context()
    assert str(caught.value) == MESSAGE
    assert str(tmp_path) not in str(caught.value)


def test_ssl_load_failure_is_sanitized_without_path(monkeypatch, tmp_path):
    secret_path = str(tmp_path / "private-location.pem")
    def boom(self, *args, **kwargs):
        raise ssl.SSLError("failed at " + secret_path)
    monkeypatch.setattr(ssl.SSLContext, "load_verify_locations", boom)
    with pytest.raises(RuntimeError) as caught:
        trusted_tls_context()
    assert str(caught.value) == MESSAGE
    assert secret_path not in str(caught.value)


def test_certifi_where_runtime_error_is_sanitized_without_fallback(monkeypatch):
    fallback_calls = []
    def where():
        raise RuntimeError(SENTINEL)
    _fake_certifi_where(monkeypatch, "2026.10.23", where)
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: fallback_calls.append((a, k)))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: fallback_calls.append((a, k)))
    with pytest.raises(RuntimeError) as caught:
        trusted_tls_context()
    _assert_sanitized(caught)
    assert fallback_calls == []


def test_load_verify_locations_runtime_error_is_sanitized_without_fallback(monkeypatch):
    fallback_calls = []
    def boom(self, *args, **kwargs):
        raise RuntimeError(SENTINEL)
    monkeypatch.setattr(ssl.SSLContext, "load_verify_locations", boom)
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: fallback_calls.append((a, k)))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: fallback_calls.append((a, k)))
    with pytest.raises(RuntimeError) as caught:
        trusted_tls_context()
    _assert_sanitized(caught)
    assert fallback_calls == []


def test_get_ca_certs_runtime_error_is_sanitized(monkeypatch):
    def boom(self, *args, **kwargs):
        raise RuntimeError(SENTINEL)
    monkeypatch.setattr(ssl.SSLContext, "get_ca_certs", boom)
    with pytest.raises(RuntimeError) as caught:
        trusted_tls_context()
    _assert_sanitized(caught)


def test_unicode_bundle_path_is_accepted(monkeypatch, tmp_path):
    import certifi
    directory = tmp_path / "Ünïcode Üser"
    directory.mkdir()
    bundle = directory / "cacert.pem"
    shutil.copyfile(certifi.where(), bundle)
    assert not str(bundle).isascii()
    _fake_certifi(monkeypatch, "2026.10.23", str(bundle))
    _assert_verified(trusted_tls_context())


def test_meta_default_transport_receives_shared_context(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", DUMMY)
    captured = {}
    class Resp:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, limit=-1):
            return json.dumps({"status": "completed", "output": [], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}).encode()
    def fake_urlopen(req, timeout=None, context=None):
        captured["context"] = context
        captured["timeout"] = timeout
        return Resp()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert MetaClient().create_response("hello")["status"] == "completed"
    _assert_verified(captured["context"])


def test_meta_tls_failure_prevents_urlopen_and_blocks_conservatively(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", DUMMY)
    calls = []
    def failing():
        raise RuntimeError(MESSAGE)
    monkeypatch.setattr(meta_client_module, "trusted_tls_context", failing)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append((a, k)))
    client = MetaClient()
    with pytest.raises(MetaClientError) as caught:
        client.create_response("hello")
    assert str(caught.value) == "meta transport failed"
    assert calls == []
    summary = client.usage_summary()
    assert summary["blocked"] is True and summary["unknown_usage_calls"] == 1
    assert summary["reserved_cost"] > 0


def test_runtime_default_opener_uses_shared_context_and_no_redirect(monkeypatch):
    seen = {}
    class FakeDirector:
        def open(self, request, timeout=None):
            seen["timeout"] = timeout
            return object()
    def fake_build_opener(*handlers):
        seen["handlers"] = handlers
        return FakeDirector()
    monkeypatch.setattr(runtime_budget_module.urllib.request, "build_opener", fake_build_opener)
    request = urllib.request.Request("https://api.meta.ai/v1/responses", data=b"{}", method="POST")
    runtime_budget_module._default_opener(request, timeout=12)
    handlers = seen["handlers"]
    assert any(isinstance(h, _NoRedirect) for h in handlers)
    https = [h for h in handlers if isinstance(h, urllib.request.HTTPSHandler)]
    assert len(https) == 1
    _assert_verified(https[0]._context)
    assert seen["timeout"] == 12


def test_runtime_tls_failure_prevents_build_opener_and_open(monkeypatch):
    calls = []
    def failing():
        raise RuntimeError(MESSAGE)
    monkeypatch.setattr(runtime_budget_module, "trusted_tls_context", failing)
    monkeypatch.setattr(runtime_budget_module.urllib.request, "build_opener", lambda *h: calls.append(h))
    request = urllib.request.Request("https://api.meta.ai/v1/responses", data=b"{}", method="POST")
    with pytest.raises(RuntimeError) as caught:
        runtime_budget_module._default_opener(request, timeout=12)
    assert str(caught.value) == MESSAGE
    assert calls == []
