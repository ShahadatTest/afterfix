# Runtime review —2026-10-28

Installed version evidence: [runtime inventory](evaluations/runtime-inventory-2026-10-28.json). This targeted review found patch gaps; it does not clear the host OS, vendor libraries or public deployment.

| Component | Observation and next condition |
| --- | --- |
| Python3.12.10 / OpenSSL3.0.16 | [Python3.12.16](https://www.python.org/downloads/release/python-31216/) adds compromised-certificate-key distrust and other security fixes. Global/control Python is also3.12.10. A patched runtime still needs isolated compatibility testing; updated pip alone does not patch Python. |
| SQLite3.49.1 / FTS5 compiled | [SQLite guidance](https://sqlite.org/cves.html) lists later arbitrary-SQL/corrupt-FTS5 fixes. Current API accepts neither caller SQL nor uploaded databases; values are bound and schema/DB paths are trusted. These input constraints reduce applicability, without proving universal immunity. |
| Node24.18.0 / npm12.0.2 | [Node24.22.1](https://nodejs.org/en/blog/release/v24.22.1/) is a security update. Node currently serves builds/localVite, rather than the Python backend. Isolated patched-toolchain rebuild remains pending. No exhaustive npm-vendored assessment is claimed. |
| Rust/Cargo1.98.0 | [Rust1.98.1](https://blog.rust-lang.org/2026/09/03/Rust-1.98.1/) fixes a trait-vtable miscompilation with undefined behavior. Passing tests do not establish its absence. Rebuild and compare under a patched compiler before release. |

The [PSF CA incident advisory](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576) recommends certifi2026.10.19 or newer and patched Python. The project already has certifi2026.10.23. The lead generation controller now uses only this reviewed, hash-checked CA bundle with certificate/hostname validation, TLS>=1.2, no inherited proxy/redirect and no session-key logging. This changes only the process-local HTTPS trust configuration; it does not modify OS trust or disable validation. A no-auth HEAD reached the official Meta endpoint over verified TLS; no key or model request was used. [Scoped evidence](evaluations/runtime-advisory-review-2026-10-28.json).

Meta will implement the equivalent bounded trust-context fix for both product HTTPS transports, with offline tests and an explicit certifi pin. This is pending, and does not replace the Python/OpenSSL/toolchain upgrade/revalidation requirement. No global install, certificate-store edit, TLS bypass, production access or deployment occurred.
