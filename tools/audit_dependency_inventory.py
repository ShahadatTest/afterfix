"""Offline-by-default dependency advisory audit against OSV querybatch.

Stdlib only. Default mode validates an inventory and writes prepared
artifacts without any network access. Network access occurs only when
``--query-osv`` is explicitly supplied (or ``query_osv=True`` is passed
programmatically). Only validated public ecosystem/name/version triples
are transmitted; local provenance (source hashes, coverage, timestamps)
never leaves the machine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://api.osv.dev/v1/querybatch"
USER_AGENT = "AfterFix-Dependency-Audit/1.0"
MAX_INVENTORY_BYTES = 256 * 1024
MAX_PACKAGES = 200
MAX_BATCH = 100
MAX_BATCHES = 2
MAX_REQUEST_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_TIMEOUT = 30.0
ECOSYSTEMS = ("PyPI", "npm", "crates.io")
ROOT_FIELDS = {"schema_version", "generated_at_utc", "coverage", "source_hashes", "packages"}
PACKAGE_FIELDS = {"ecosystem", "name", "version"}
REQUEST_NAME = "request.json"
RESULTS_NAME = "results.json"
SUMMARY_NAME = "summary.json"

# All patterns are used with fullmatch(): no prefix, trailing-newline or
# partial-string acceptance is possible.
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-!]{0,127}")
_PYPI_NAME_RE = re.compile(r"[A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]")
_CRATE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_NPM_PART_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._~-]*[A-Za-z0-9_~-])?")
_ADVISORY_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,127}")
_MODIFIED_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+\-]{0,63}")
_HASH_RE = re.compile(r"[0-9a-f]{64}")


class AuditError(Exception):
    """Error carrying only a fixed, sanitized code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _BatchFailure(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _fail(code: str):
    raise AuditError(code)


def _no_duplicates(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            _fail("invalid_inventory")
        obj[key] = value
    return obj


def _no_duplicates_response(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate key")
        obj[key] = value
    return obj


def _bounded_str(value, lo, hi):
    return isinstance(value, str) and lo <= len(value) <= hi and not any(
        ord(c) < 0x20 or ord(c) == 0x7F for c in value
    )


def _strict_token_str(value, lo, hi):
    """Printable ASCII only: no whitespace, controls or non-ASCII."""
    return (
        isinstance(value, str)
        and lo <= len(value) <= hi
        and all(0x21 <= ord(c) <= 0x7E for c in value)
    )


def _validate_name(ecosystem: str, name) -> None:
    # Whole-string, strict ASCII printable check first; per-ecosystem
    # grammar then applies without generic filters contradicting it
    # (notably, the scoped-npm slash is handled only by the npm grammar).
    if not _strict_token_str(name, 1, 214):
        _fail("invalid_package")
    if ecosystem == "PyPI":
        if not _PYPI_NAME_RE.fullmatch(name):
            _fail("invalid_package")
    elif ecosystem == "crates.io":
        if not _CRATE_NAME_RE.fullmatch(name):
            _fail("invalid_package")
    elif ecosystem == "npm":
        if name.startswith("@"):
            rest = name[1:]
            parts = rest.split("/")
            if len(parts) != 2 or "@" in rest:
                _fail("invalid_package")
            if not all(_NPM_PART_RE.fullmatch(p) and ".." not in p for p in parts):
                _fail("invalid_package")
        else:
            if "/" in name or "@" in name:
                _fail("invalid_package")
            if not _NPM_PART_RE.fullmatch(name) or ".." in name:
                _fail("invalid_package")
    else:  # pragma: no cover - guarded by caller
        _fail("invalid_package")


def _validate_version(version) -> None:
    if not _strict_token_str(version, 1, 128) or not _VERSION_RE.fullmatch(version):
        _fail("invalid_package")


def load_inventory(path: Path):
    """Return (inventory_sha256, deduplicated sorted package list)."""
    try:
        size = path.stat().st_size
    except OSError:
        _fail("inventory_unreadable")
    if size > MAX_INVENTORY_BYTES or size == 0:
        _fail("inventory_too_large" if size else "invalid_inventory")
    try:
        raw = path.read_bytes()
    except OSError:
        _fail("inventory_unreadable")
    if len(raw) > MAX_INVENTORY_BYTES:
        _fail("inventory_too_large")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except AuditError:
        raise
    except Exception:
        _fail("invalid_inventory")
    if not isinstance(data, dict) or set(data.keys()) != ROOT_FIELDS:
        _fail("invalid_inventory")
    # Exactly integer 1: bool is a subclass of int and float 1.0 compares
    # equal, so an exact type check is required.
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        _fail("invalid_inventory")
    if not _bounded_str(data["generated_at_utc"], 1, 64):
        _fail("invalid_inventory")
    if not _bounded_str(data["coverage"], 1, 512):
        _fail("invalid_inventory")
    hashes = data["source_hashes"]
    if not isinstance(hashes, dict) or not (1 <= len(hashes) <= 16):
        _fail("invalid_inventory")
    for key, value in hashes.items():
        if not _bounded_str(key, 1, 128) or not isinstance(value, str) or not _HASH_RE.fullmatch(value):
            _fail("invalid_inventory")
    packages = data["packages"]
    if not isinstance(packages, list) or not (1 <= len(packages) <= MAX_PACKAGES):
        _fail("invalid_inventory")
    seen = set()
    unique = []
    for entry in packages:
        if not isinstance(entry, dict) or set(entry.keys()) != PACKAGE_FIELDS:
            _fail("invalid_package")
        eco, name, version = entry["ecosystem"], entry["name"], entry["version"]
        if eco not in ECOSYSTEMS:
            _fail("invalid_package")
        _validate_name(eco, name)
        _validate_version(version)
        key = (eco, name, version)
        if key not in seen:
            seen.add(key)
            unique.append({"ecosystem": eco, "name": name, "version": version})
    unique.sort(key=lambda p: (p["ecosystem"], p["name"], p["version"]))
    if not unique:
        _fail("invalid_inventory")
    return digest, unique


def build_queries(packages):
    return [
        {"package": {"ecosystem": p["ecosystem"], "name": p["name"]}, "version": p["version"]}
        for p in packages
    ]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _real_transport(url, body, headers, timeout):
    if url != ENDPOINT:
        raise _BatchFailure("transport_error")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            data = response.read(MAX_RESPONSE_BYTES + 1)
            return status, data
    except urllib.error.HTTPError as exc:
        # Error bodies are unneeded and untrusted: never read them (an
        # unbounded read could consume arbitrary data). Just close/discard.
        try:
            exc.close()
        except Exception:
            pass
        return exc.code, b""
    except Exception:
        raise _BatchFailure("network_error")


def _check_pagination_token(payload, holder):
    """Raise for a pagination token present on payload/holder, if any."""
    if "next_page_token" not in holder:
        return
    token = holder["next_page_token"]
    if _strict_token_str(token, 1, 512):
        raise _BatchFailure("pagination")
    raise _BatchFailure("invalid_response")


def _validate_batch_response(payload, expected_count):
    if not isinstance(payload, dict):
        raise _BatchFailure("invalid_response")
    if set(payload.keys()) - {"results", "next_page_token"}:
        raise _BatchFailure("invalid_response")
    if "error" in payload:
        raise _BatchFailure("invalid_response")
    _check_pagination_token(payload, payload)
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != expected_count:
        raise _BatchFailure("invalid_response")
    validated = []
    for item in results:
        if not isinstance(item, dict) or set(item.keys()) - {"vulns", "next_page_token"}:
            raise _BatchFailure("invalid_response")
        # OSV querybatch puts next_page_token on individual result items.
        _check_pagination_token(payload, item)
        vulns = item.get("vulns", [])
        if not isinstance(vulns, list):
            raise _BatchFailure("invalid_response")
        clean = []
        for vuln in vulns:
            if not isinstance(vuln, dict) or set(vuln.keys()) != {"id", "modified"}:
                raise _BatchFailure("invalid_response")
            vid, modified = vuln["id"], vuln["modified"]
            if not isinstance(vid, str) or not _ADVISORY_ID_RE.fullmatch(vid):
                raise _BatchFailure("invalid_response")
            if not isinstance(modified, str) or not _MODIFIED_RE.fullmatch(modified):
                raise _BatchFailure("invalid_response")
            clean.append({"id": vid, "modified": modified})
        validated.append(clean)
    return validated


def _query_batch(transport, batch_packages, timeout):
    body = json.dumps({"queries": build_queries(batch_packages)}, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:
        raise _BatchFailure("request_too_large")
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    try:
        status, data = transport(ENDPOINT, body, headers, timeout)
    except _BatchFailure:
        raise
    except Exception:
        raise _BatchFailure("network_error")
    if status != 200:
        raise _BatchFailure("http_error")
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_RESPONSE_BYTES:
        raise _BatchFailure("response_too_large")
    try:
        payload = json.loads(bytes(data).decode("utf-8"), object_pairs_hook=_no_duplicates_response)
    except Exception:
        raise _BatchFailure("invalid_response")
    return _validate_batch_response(payload, len(batch_packages))


def _prepare_output(output_dir: Path) -> None:
    parent = output_dir.parent
    if str(parent) == "" or not parent.is_dir() or parent.is_symlink():
        _fail("invalid_output")
    if output_dir.exists() or output_dir.is_symlink():
        _fail("output_exists")
    try:
        output_dir.mkdir(exist_ok=False)
    except FileExistsError:
        _fail("output_exists")
    except OSError:
        _fail("invalid_output")


def _write_json(output_dir: Path, filename: str, obj) -> None:
    text = json.dumps(obj, indent=2, sort_keys=True) + "\n"
    try:
        with open(output_dir / filename, "x", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        _fail("output_write_failed")


def run_audit(inventory_path, output_dir, query_osv=False, transport=None, timeout=MAX_TIMEOUT):
    inventory_path = Path(inventory_path)
    output_dir = Path(output_dir)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not (0 < timeout <= MAX_TIMEOUT):
        _fail("invalid_timeout")
    digest, packages = load_inventory(inventory_path)
    # All validation happens before any output creation or network use.
    _prepare_output(output_dir)

    queries = build_queries(packages)
    per_package = [
        {"ecosystem": p["ecosystem"], "name": p["name"], "version": p["version"],
         "queried": False, "vulns": []}
        for p in packages
    ]
    status = "not_queried"
    reason = None
    queried_count = 0

    if query_osv:
        active_transport = transport if transport is not None else _real_transport
        batches = [packages[i:i + MAX_BATCH] for i in range(0, len(packages), MAX_BATCH)]
        if len(batches) > MAX_BATCHES:
            reason = "too_many_batches"
            status = "incomplete"
        else:
            offset = 0
            failed = False
            for batch in batches:
                try:
                    validated = _query_batch(active_transport, batch, float(timeout))
                except _BatchFailure as exc:
                    reason = exc.reason
                    failed = True
                    break
                for idx, vulns in enumerate(validated):
                    per_package[offset + idx]["queried"] = True
                    per_package[offset + idx]["vulns"] = vulns
                queried_count += len(batch)
                offset += len(batch)
            if failed:
                status = "incomplete"
            else:
                instances = sum(len(p["vulns"]) for p in per_package)
                status = "findings" if instances else "complete_no_known_advisories"

    instances = sum(len(p["vulns"]) for p in per_package if p["queried"])
    unique_ids = {v["id"] for p in per_package if p["queried"] for v in p["vulns"]}
    complete = status in ("complete_no_known_advisories", "findings")
    partial_counts = status == "incomplete" and queried_count > 0
    summary = {
        "status": status,
        "inventory_sha256": digest,
        "total_count": len(packages),
        "queried_count": queried_count,
        "advisory_instance_count": instances if (complete or partial_counts) else None,
        "advisory_unique_count": len(unique_ids) if (complete or partial_counts) else None,
        "counts_partial": bool(partial_counts),
    }
    if reason is not None:
        summary["reason"] = reason
    results = {
        "status": status,
        "inventory_sha256": digest,
        "packages": per_package,
    }
    if reason is not None:
        results["reason"] = reason
    request_doc = {"queries": queries}
    _write_json(output_dir, REQUEST_NAME, request_doc)
    _write_json(output_dir, RESULTS_NAME, results)
    _write_json(output_dir, SUMMARY_NAME, summary)

    if status == "not_queried" or status == "complete_no_known_advisories":
        return 0
    if status == "findings":
        return 1
    return 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate a dependency inventory; optionally query OSV.")
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--query-osv", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run_audit(Path(args.inventory), Path(args.output), query_osv=args.query_osv)
    except AuditError as exc:
        print(f"error: {exc.code}", file=sys.stderr)
        return 2
    except Exception:
        print("error: internal_error", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
