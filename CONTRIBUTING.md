# Contributing to AfterFix

Start with the [project overview](README.md), [supported connector contract](docs/INSTALL_AGENT.md#supported-sqlite-contract) and [architecture](docs/ARCHITECTURE.md). Please submit contributions under the project’s [MIT License](LICENSE).

## Propose a change

Use an issue for a reproducible bug or a focused feature proposal. Explain the user problem, the supported data shape and the expected result. For inventory examples, use synthetic records and state the opening stock, order quantities and later valid activity. Never attach a real inventory file, access key, provider key or private runtime ledger.

For sensitive findings, follow [SECURITY.md](SECURITY.md).

## Develop and validate

Keep changes focused and preserve unrelated files. The [development guide](docs/DEVELOPMENT.md) documents the Docker QA target, frontend native checks and manual environment setup. Live model calls are not required for deterministic tests and must never be started as an implicit install/test step.

For workflow changes, test the relevant boundary: unsupported evidence, stale state, actor approval, apply retries, preservation, or session changes. For UI-only changes, use the frontend build and focused browser checks. Record the commands actually run and any failures or limitations.

## Submit a pull request

Describe the problem and resulting behavior. State how you validated it and whether any model response was live, replayed or injected offline. Keep historical failed evaluations intact; a new fixture must not replace a frozen live result.

Changes to schema, migrations, the supported connector contract, cost guards or approval/apply semantics need a clear explanation of their effect on existing installations. Never introduce automatic approval, arbitrary model SQL/shell tools, a silent paid retry/fallback or an unreviewed production-data path.

The existing Linux workflow is manually dispatched. A local result does not imply a GitHub Actions run, and the repository's reported test counts are dated snapshots.
