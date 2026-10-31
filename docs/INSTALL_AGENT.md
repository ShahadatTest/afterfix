# Install the local inventory agent

AfterFix can run beside a **compatible SQLite inventory file**, scan its recorded events, and use the existing investigation → preview → approval → apply → verification flow on that file. This is a bounded connector, not an arbitrary SQL repair engine. One product is selected per installation. Use an isolated copy first; this release has been tested with fixtures, not a customer's production application.

The public website remains a separate synthetic demo. Do not upload your database or provider key there. The agent publishes its UI only on your computer's `127.0.0.1`. There is no standalone login page; use **Operator access** inside the dashboard to unlock private inventory data.

## Requirements

- Git and Python 3.12+ on the host; the installer uses Python's standard library.
- Docker with Linux containers, running and accessible to your user. Python/FastAPI, the compiled Rust verifier and the production frontend run inside one image; no host Node or Rust installation is needed.
- Internet access for the first Docker build. Installation/build/scan do not call a model. Docker itself may require separate installation/terms on your platform; the installer does not install or configure it.
- A compatible inventory file, a real UTF-8 code-fix patch of at most 8000 bytes, and permission to extend the file's schema.
- A new private installation directory **outside** the inventory directory. Keep the SQLite file in a dedicated directory because SQLite needs writable journal files beside it. The container mounts that directory read/write, and mounts the private configuration read-only.

## Install and connect

After cloning this repository, run from its root:

```sh
python -m app.agent install
```

The terminal asks for the inventory file, code-fix patch, new private directory and product ID. It explains the schema extension and requires you to type `INSTALL`. No credential is printed. The installer validates the source, builds the image, checks directory write permission, makes an integrity-checked SQLite backup, extends the schema, writes private configuration, and starts the agent. Startup scans automatically; it never starts an AI request or a repair.

For a noninteractive Linux invocation (replace the paths/product with your own):

```sh
python -m app.agent install --database /srv/inventory/inventory.sqlite --patch /srv/changes/fix.patch --product PART-A --directory /srv/afterfix-private --consent-schema-extension
```

Windows PowerShell uses the same CLI, for example:

```powershell
python -m app.agent install --database 'D:\Inventory\inventory.sqlite' --patch 'D:\Changes\fix.patch' --product PART-A --directory 'D:\AfterFixPrivate' --consent-schema-extension
```

Docker must have access to those drives. The installer restricts the newly created private Windows directory to the current Windows identity and SYSTEM. Linux uses mode0700 for the directory and0600 for its files. The container runs as your nonroot Linux UID/GID, or10001:10001 on Windows/root-host invocation. If that identity cannot write the selected SQLite file and dedicated directory, setup stops before schema extension. Fix only those dedicated-directory permissions; do not make a home directory/world-writable tree accessible. Windows Docker filesystem behavior requires a platform-specific smoke check; do not assume Linux results establish Windows Docker compatibility.

Options: `--port 18157` selects an unused local port; `--no-build` uses a previously reviewed `afterfix-agent:local` image; `--no-start` prepares the database/private configuration without Docker startup. Preparation is not proof of a running installation. Existing installation directories, backups and reserved metadata tables are never silently overwritten.

## Open, scan and repair

1. Open `http://127.0.0.1:18157` (or your selected port).
2. Open your private `access.env` locally and copy only the `AFTERFIX_OPERATOR_TOKEN` value into **Operator access**. Do not send it to chat or publish it. The Meta key is different and never belongs in this dialog.
3. The **Connected inventory** panel shows the startup scan. **Scan inventory** rechecks the source and updates the incident metadata. It changes neither stock nor business event rows. Scans are on startup/on demand, not an unattended continuous monitor.
4. A candidate is a finding to investigate, not an approved fix. With a configured Meta runtime, explicitly select **Run investigation**. Only the bounded code patch and requested evidence are sent to Meta; the full SQLite file is not uploaded to the shared website.
5. Review the evidence and computed preview. Python and the mandatory Rust verifier must agree. Check the review box, approve, then separately apply. Existing actor/snapshot/idempotency/transaction guards remain in place.
6. Verify and download the case report. A changed source invalidates the old approval; rescan and investigate again. A repeated apply request cannot create a second repair.

Missing/inconsistent opening evidence, contradictory order quantities, stock/ledger disagreement or unsupported rows produce a review/connection error. There is no guessed repair. A clean scan means no outstanding supported duplicate was found; it does not certify the whole database.

## Supported SQLite contract

Required existing business tables/columns:

| Table | Required fields |
| --- | --- |
| `products` | `product_id` primary key, `name` text, `stock` integer |
| `events` | `id INTEGER PRIMARY KEY`, `product_id`, `kind`, `qty`, `order_id`, `ts`, `note` |

Semantics: one opening `init` event comes first; `order_deduction` reduces stock, `restock` increases it. Repeated identical `order_id` values mean delivery of the **same business order** for the selected product, with identical positive quantity. Split shipments or intentionally repeated order IDs are not this contract. A code fix must already prevent the bug recurring. AfterFix repairs persisted data; it does not deploy your code fix.

Current bounds:100MiB file,50 products,200 events across the file, positive safe-integer event IDs/quantities/timestamps (opening quantity may be zero), selected product IDs bounded to64 characters and product names/order identifiers to128 characters and notes to500. Views, triggers, virtual tables, reserved AfterFix table collisions and unsupported evidence are rejected. Applications with required extra event columns/constraints may reject the repair transaction; no successful repair is assumed. Test the full flow on a copy of your schema.

**Schema change requiring consent:** the installer adds `events.reversed_ids` and `events.repair_proposal_id` when absent, plus AfterFix incident/proposal/approval/idempotency/audit/case/installation metadata tables. It preserves existing stock and event values. Your application must insert using **named columns**, not `INSERT INTO events VALUES (...)`, because adding columns changes the latter statement's expected arity. This is an integration requirement, not transparent compatibility with every SQLite application.

The initial full backup is `before-install.sqlite` in the private directory. Stop the writer/application and review later valid activity before restoring it; restoring the entire old file could discard subsequent real events. A failed installation does not automatically replace or restore the source. Never rerun setup against a partially prepared source without reviewing the backup/state.

Runtime connections verify the installation identity and schema digest before use; they do not auto-migrate a replacement file. The code patch is copied and hash-bound. Changing the schema or patch requires reconnection/review, not silently using a demo patch. These checks are not protection against a local administrator rewriting every record, configuration and digest.

## Enable Meta explicitly

The default installation has **no provider credential or fabricated credit ledger**. Scanning works, but AI investigation returns unavailable until both key and verified accounting are provisioned.

- Put the server-side `MODEL_API_KEY` privately in the installation's `model.env`; this file accepts only that variable, never application tokens or configuration overrides.
- Provision the existing verified shared ledger files under the private `runtime` directory according to [RUNTIME.md](RUNTIME.md). Use the project's actual ledger/allocation; never reset usage or create a fresh zero-usage ledger for an existing project. Keep the$100 project cap,$50 reserve and24-hour freshness checks. Provider billing must be cleared and promotional use verified first.
- No automatic retries, paid fallback, overage, subscriptions or training mode are enabled.
- After changing runtime settings, remove only the stopped owned container and recreate it using the commands below. An ordinary `start` of an existing container retains its original environment.

## Start, stop and configuration changes

```sh
python -m app.agent start --directory /srv/afterfix-private --no-build
python -m app.agent stop --directory /srv/afterfix-private
```

To recreate after a reviewed configuration change:

```sh
python -m app.agent stop --directory /srv/afterfix-private --remove
python -m app.agent start --directory /srv/afterfix-private --no-build
```

Only the container with this installation's name **and matching ownership label** may be started/stopped/removed. The database, backup, configuration and runtime ledger are retained. No Docker prune or unrelated service cleanup occurs. The container has a read-only root, no Linux capabilities, no-new-privileges, bounded CPU/memory/PIDs, and only a loopback published port. Public synthetic reads, reset and Scenario Lab are forcibly disabled in connected mode even if flags are accidentally enabled.

There is no auto-start OS service, automatic updater, PostgreSQL connector, general schema mapping, multi-tenant isolation or arbitrary SQL/model execution. This release's evidence is an isolated install/scan/recovery test, not production readiness or a security certification.
