# Dependency advisory snapshot —2026-10-28

A completed public OSV query covered all103 pinned public package triples.12 distinct advisory IDs were returned, all for the fresh-clone installer `pip25.0.1`; six GHSA/PYSEC alias pairs describe six underlying issues. The other102 triples had no indexed matches in this snapshot. This is not a security certification or a claim about unknown issues.

Raw request/results/summary are byte-preserved in [osv-dependencies-v1](evaluations/osv-dependencies-v1/lead-review.json); mappings, inventory/source hashes and counts were independently checked. Exit1 means completed with findings, not query failure. No package was upgraded.

| Advisory and alias | First fixed pip | Installation applicability |
| --- | --- | --- |
| [GHSA-4xh5-x5gv-qwph](https://osv.dev/vulnerability/GHSA-4xh5-x5gv-qwph) / PYSEC-2026-1795 / CVE-2025-8869 | 25.3 | tar symlink fallback; Python3.12 implements PEP706, so described fallback not used. |
| [GHSA-58qw-9mgm-455v](https://osv.dev/vulnerability/GHSA-58qw-9mgm-455v) / PYSEC-2026-2875 / CVE-2026-3219 | 26.1 | ambiguous concatenated ZIP/tar installation. |
| [GHSA-6vgw-5pg2-w6jp](https://osv.dev/vulnerability/GHSA-6vgw-5pg2-w6jp) / PYSEC-2026-1796 / CVE-2026-1703 | 26.0 | crafted wheel extraction can escape target prefixes. |
| [GHSA-jp4c-xjxw-mgf9](https://osv.dev/vulnerability/GHSA-jp4c-xjxw-mgf9) / PYSEC-2026-2876 / CVE-2026-6357 | 26.1 | self-update imports after wheel installation; disable-pip-version-check mitigates reviewed install path only. |
| [GHSA-qwm4-qh6w-59xr](https://osv.dev/vulnerability/GHSA-qwm4-qh6w-59xr) / PYSEC-2026-3721 / CVE-2026-13346 | 26.2.0 | double-encoded URLs require malicious package index; fixed PyPI source reduces this exposure. |
| [GHSA-wf93-45jw-7689](https://osv.dev/vulnerability/GHSA-wf93-45jw-7689) / PYSEC-2026-196 / CVE-2026-8643 | 26.1.2 | console/gui entry point path traversal during package installation. |

These are installer exposures. The application does not import pip as part of its recovery runtime. Crafted packages and install-time conditions still matter; using an isolated environment alone does not fix path traversal. Reviewed clone setup already used explicit PyPI and disabled the version check, reducing specific preconditions, without establishing immunity or ruling out past exploitation.

Next candidate: [pip26.2.2 on PyPI](https://pypi.org/project/pip/26.2.2/) ([official changelog](https://pip.pypa.io/en/stable/news/)). Lead verified the public wheel SHA256 `fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70`. Meta will prepare a separate hash-pinned installer requirement and setup text. It is not yet installed or re-queried. Preserve the original inventory/results when recording any later environment.

OS/SQLite/toolchain advisories, pip vendored libraries, final secret review, mobile checks and release preparation remain separate work. No new Meta calls, dependency install, global configuration, deployment or production access occurred during this snapshot review.

## Verified installer update and second snapshot

The reviewed hash-pinned bootstrap actually upgraded only the project's `.venv` installer from pip25.0.1 to26.2.2. The other23 Python distributions remained unchanged. The explicit project interpreter reported26.2.2, and `pip check` reported no broken requirements. No global Python or application requirement was changed or reinstalled.

A new actual103-triple public OSV query completed with zero indexed advisory matches and no partial counts. Lead independently verified the before/after packages, inventory delta, all103 request/result mappings, queried flags and source/inventory hashes. [Version2 evidence](evaluations/bootstrap-verified-v2/lead-review.json) preserves all artifacts; the original12-ID/six-issue snapshot above remains unchanged.

This validates the bootstrap command and current project environment. It does not revalidate an entire fresh checkout or remove oldpip from retained historical QA clones. The application-requirements installation command was not rerun. Backend403/Rust32/browser evidence was not repeated for this installer-only change. OS/SQLite/toolchain and pip vendored-library advisories, unknown issues, secret review and mobile checks remain separate work. Zero indexed matches is not a security certification.
