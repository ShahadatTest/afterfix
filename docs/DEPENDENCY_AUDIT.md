# Dependency audit

A known-advisory snapshot tool for the lead-reviewed, public-only inventory
`docs/evaluations/dependency-inventory-2026-10-28.json` (103 ecosystem/name/
version triples: PyPI, npm, and crates.io). It is not a security certification
and does not audit malware, application/OS/SQLite code, or toolchain issues.
Snapshot results may miss unknown or newly published advisories, and a match
does not by itself prove exploitability.

## Usage

From the repository root (Windows example):

```
.venv/Scripts/python.exe tools/audit_dependency_inventory.py --inventory docs/evaluations/dependency-inventory-2026-10-28.json --output <NEW_EXISTING_PARENT/outputdir>
```

The output directory must not already exist; its parent must exist. The tool
creates it exclusively and writes only `request.json`, `results.json`, and
`summary.json`. It never overwrites, deletes, or cleans up existing files.

Default mode validates and prepares artifacts only — **no network access** —
and reports status `not_queried`. That is not a claim that packages are clean.

To perform the separately lead-authorized live query, add the explicit opt-in:

```
.venv/Scripts/python.exe tools/audit_dependency_inventory.py --inventory docs/evaluations/dependency-inventory-2026-10-28.json --output <NEW_EXISTING_PARENT/outputdir> --query-osv
```

## Network behavior (opt-in only)

Only validated public `ecosystem`/`name`/`version` triples are sent, as
`{"queries":[{"package":{"ecosystem":"...","name":"..."},"version":"..."}]}`
to the fixed HTTPS endpoint `https://api.osv.dev/v1/querybatch`
([OSV querybatch API](https://google.github.io/osv.dev/post-v1-querybatch/)).
Local provenance (`source_hashes`, coverage, timestamps) is never transmitted.
No credentials, environment values, config files, subprocesses, arbitrary
endpoints, redirects, inherited proxies, retries, or follow-up detail fetches
are used. At most 100 queries per call (103 packages → 2 calls), 30 s timeout
per call, and a 4 MiB response cap. If OSV returns a pagination token, the run
stops as `incomplete` rather than claiming a complete result.

## Outcomes

| Status | Exit code | Meaning |
| --- | --- | --- |
| `not_queried` | 0 | Default offline validation/preparation only. No clean claim. |
| `complete_no_known_advisories` | 0 | All queries completed; no indexed matches at query time. |
| `findings` | 1 | Completed; advisory IDs require lead review. No upgrades are made. |
| `incomplete` | 2 | A batch failed (HTTP/network/malformed/pagination/oversize). Earlier completed batch mappings are preserved; never read as zero findings. |
| invalid input/existing output | 2 | Preflight rejection before network use, with a fixed sanitized error code. |
| output write failure | 2 | An artifact write failed; some output may remain and a query may already have completed. Never infer zero findings. |

`summary.json` records the inventory SHA-256, queried/total counts, status,
and — only for complete runs or explicitly marked partial counts — unique
advisory-ID and advisory-instance counts. `results.json` retains the validated
per-package query-to-advisory (`id`, `modified`) mapping. Batch responses
contain IDs and modification times only; severity and fixed-version
interpretation, and inspection of the underlying advisory records, are
separate lead steps.

## Limits

Inventory input is capped at 256 KiB and 200 packages, with strict schema,
field, ecosystem, name, and version validation; secret-like extra fields are
rejected. Tests are offline only (`tests/test_dependency_audit.py`) using
injected fake transports; no actual OSV result is claimed until the lead
reviews this source, runs the focused offline tests, and separately
authorizes and executes one bounded public query.
