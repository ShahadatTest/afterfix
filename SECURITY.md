# Security

AfterFix is experimental. Its recorded checks cover a bounded inventory connector and isolated synthetic fixtures; they are not a penetration test or security certification. See the [architecture](docs/ARCHITECTURE.md), [focused review](docs/SECURITY_REVIEW.md) and [current state](docs/STATE.md).

## Reporting

GitHub private vulnerability reporting and a private reporting contact are not currently configured. If you suspect a sensitive issue, open a minimal issue requesting a private contact, without exploit details, real records or credentials. Agree on a private channel with the maintainer before sharing sensitive material.

Ordinary non-sensitive bugs can use the repository's bug-report form with synthetic reproduction data.

## Deployment boundaries

- The local agent binds to loopback and requires a private application key to unlock inventory. Its Meta key stays server-side and is separate from the application access key.
- Schema extension requires explicit installation consent and a verified backup. Test compatibility on an isolated copy first.
- A repair needs evidence, deterministic Python/Rust agreement, actor-bound approval, a current snapshot and a separate transactional apply. The model has no SQL/shell/apply tool.
- The hosted public preview exposes only explicitly allowed synthetic reads. It is a shared demo, not a tenant-isolated service for customer data.
- Credit reservations, the project cap/reserve and freshness checks must remain enabled. A model-provider failure must not enable paid fallback or weaker verification.

Do not publish keys, environment files, inventory databases or private accounting state in an issue, pull request, screenshot or log. The current dependency/security evidence is dated; it does not cover unknown issues or every operating-system/toolchain component.
