"""Bounded SQLite inventory connector and local Docker installer.

Only installation adds schema, with explicit consent and a verified backup.
Scanning changes AfterFix incident metadata, never inventory or event rows.
Repair remains the existing Meta/Python/Rust/approval transaction pipeline.
"""
import argparse
import contextlib
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.error

from app import domain, store

MAX_DB_BYTES = 100 * 1024 * 1024
MAX_PRODUCTS = 50
MAX_EVENTS = 200
RESERVED = {"incidents", "proposals", "approvals", "idempotency_keys", "audit_log", "case_records", "afterfix_agent_installation"}
CORE = {"products": {"product_id", "name", "stock"}, "events": {"id", "product_id", "kind", "qty", "order_id", "ts", "note"}}


class AgentError(Exception):
    """Messages are fixed and contain no source paths, contents or credentials."""


def enabled():
    return os.environ.get("AFTERFIX_AGENT_MODE") == "1"


def _schema_digest(conn):
    rows = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    return hashlib.sha256(json.dumps([list(r) for r in rows], separators=(",", ":")).encode()).hexdigest()


def _check_schema(conn, *, fresh=False):
    objects = conn.execute("SELECT type,name,sql FROM sqlite_master").fetchall()
    if any(r[0] in {"view", "trigger"} for r in objects):
        raise AgentError("Views and triggers are not supported by this connector.")
    if any(re.match(r"\s*CREATE\s+VIRTUAL\s+TABLE", r[2] or "", re.I) for r in objects):
        raise AgentError("Virtual tables are not supported by this connector.")
    if fresh and any(r[1].lower() in RESERVED for r in objects):
        raise AgentError("This database already contains reserved AfterFix tables; use its existing installation.")
    for table, required in CORE.items():
        rows = conn.execute("PRAGMA table_info(" + table + ")").fetchall()
        if not required.issubset({r[1] for r in rows}):
            raise AgentError("Unsupported inventory schema. See the SQLite connector contract.")
        if table == "events" and not any(r[1] == "id" and r[2].upper() == "INTEGER" and r[5] == 1 for r in rows):
            raise AgentError("Event IDs must be INTEGER PRIMARY KEY values.")
        if table == "products" and not any(r[1] == "product_id" and r[5] == 1 for r in rows):
            raise AgentError("Product IDs must be primary keys.")
    if conn.execute("SELECT count(*) FROM products").fetchone()[0] > MAX_PRODUCTS:
        raise AgentError("This connector supports at most 50 products.")
    if len(conn.execute("SELECT 1 FROM events LIMIT ?", (MAX_EVENTS + 1,)).fetchall()) > MAX_EVENTS:
        raise AgentError("This connector supports at most 200 events across the inventory file.")


def _product(conn, product_id):
    if not isinstance(product_id, str) or not 1 <= len(product_id) <= 64 or any(ord(c) < 32 for c in product_id):
        raise AgentError("Choose a valid product ID.")
    product = conn.execute("SELECT product_id,name,stock FROM products WHERE product_id=?", (product_id,)).fetchone()
    if product is None:
        raise AgentError("The selected product does not exist.")
    if not isinstance(product["name"], str) or not 1 <= len(product["name"]) <= 128:
        raise AgentError("Product names must contain 1–128 characters.")
    if not isinstance(product["stock"], int) or abs(product["stock"]) > 2**53 - 1:
        raise AgentError("The recorded stock is outside supported integer bounds.")
    rows = conn.execute("SELECT * FROM events WHERE product_id=? ORDER BY id LIMIT ?", (product_id, MAX_EVENTS + 1)).fetchall()
    if len(rows) > MAX_EVENTS:
        raise AgentError("This connector supports at most 200 events for the selected product.")
    for row in rows:
        if (not isinstance(row["id"], int) or not 0 < row["id"] <= 2**53 - 1
            or row["kind"] not in domain.KNOWN_KINDS
            or not isinstance(row["qty"], int) or not 0 <= row["qty"] <= 2**53 - 1
            or not isinstance(row["ts"], int) or not 0 <= row["ts"] <= 2**53 - 1
            or not isinstance(row["note"], (str, type(None))) or len(row["note"] or "") > 500
            or (row["kind"] != "init" and row["qty"] == 0)
            or (row["kind"] == "order_deduction" and (not isinstance(row["order_id"], str) or not 1 <= len(row["order_id"]) <= 128))):
            raise AgentError("Unsupported or malformed event evidence; manual review is required.")
    return product, rows


def inspect_source(path, product_id=None):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_size > MAX_DB_BYTES:
        raise AgentError("Select a SQLite file no larger than 100 MiB.")
    with contextlib.closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA trusted_schema=OFF")
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
        deadline = time.monotonic() + 5
        _check_schema(conn, fresh=True)
        ids = [r[0] for r in conn.execute("SELECT product_id FROM products ORDER BY product_id")]
        if product_id is None:
            if len(ids) != 1:
                raise AgentError("Specify --product: exactly one product is selected per installation.")
            product_id = ids[0]
        _product(conn, product_id)
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise AgentError("SQLite integrity check failed.")
    return path, product_id


def initialize_database(path, product_id, backup, installation_id):
    """Explicit installation only. The caller must already own schema consent."""
    path, product_id = inspect_source(path, product_id)
    backup = Path(backup)
    if backup.exists():
        raise AgentError("The backup already exists; installation will not overwrite it.")
    with contextlib.closing(sqlite3.connect(path, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA trusted_schema=OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            _check_schema(conn, fresh=True)
            _product(conn, product_id)
            # A separate read-only snapshot avoids backup() on our write lock.
            with contextlib.closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
                with contextlib.closing(sqlite3.connect(backup)) as target:
                    source.backup(target)
                    if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise AgentError("Backup integrity check failed.")
            backup.chmod(0o600)
            for statement in store.SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            columns = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
            if "reversed_ids" not in columns:
                conn.execute("ALTER TABLE events ADD COLUMN reversed_ids TEXT NOT NULL DEFAULT '[]'")
            if "repair_proposal_id" not in columns:
                conn.execute("ALTER TABLE events ADD COLUMN repair_proposal_id TEXT")
            conn.execute("CREATE TABLE afterfix_agent_installation(id TEXT PRIMARY KEY, product_id TEXT NOT NULL, schema_digest TEXT NOT NULL)")
            conn.execute("INSERT INTO afterfix_agent_installation VALUES (?,?,?)", (installation_id, product_id, _schema_digest(conn)))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def guard_connection(conn):
    """Never auto-migrate a connected database or trust a replacement file."""
    conn.execute("PRAGMA trusted_schema=OFF")
    deadline = time.monotonic() + 5
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        row = conn.execute("SELECT id,product_id,schema_digest FROM afterfix_agent_installation").fetchall()
        if (len(row) != 1 or row[0][0] != os.environ.get("AFTERFIX_AGENT_ID")
            or row[0][1] != os.environ.get("AFTERFIX_AGENT_PRODUCT") or row[0][2] != _schema_digest(conn)):
            raise AgentError("The connected database changed identity or schema; reconnect after review.")
        _check_schema(conn)
    except sqlite3.DatabaseError:
        raise AgentError("The connected database is not initialized for this agent.") from None

    def authorize(action, _a, _b, _db, source):
        if source is not None or action in {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    conn.set_progress_handler(None, 0)
    conn.set_authorizer(authorize)


def configured_patch():
    try:
        path = Path(os.environ["AFTERFIX_AGENT_PATCH"])
        if not path.is_absolute() or path.stat().st_size > 8000:
            raise AgentError("The configured code patch is unavailable.")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != os.environ.get("AFTERFIX_AGENT_PATCH_SHA256"):
            raise AgentError("The code patch changed; reconnect after review.")
        text = raw.decode("utf-8")
        if not text.strip():
            raise AgentError("A code fix patch is required before investigation.")
        return text
    except (KeyError, OSError, UnicodeError):
        raise AgentError("The configured code patch is unavailable.") from None


def scan(*, register=False):
    if not enabled():
        raise AgentError("No local inventory connector is configured.")
    configured_patch()
    conn = store.connect()
    try:
        conn.execute("BEGIN IMMEDIATE" if register else "BEGIN")
        product_id = os.environ.get("AFTERFIX_AGENT_PRODUCT")
        product, rows = _product(conn, product_id)
        ledger = domain.compute_ledger(conn, product_id)
        from app import recovery
        _, repair_errors, _ = recovery._repair_state(conn, product_id)
        opening = [r for r in rows if r["kind"] == "init"]
        status, reason = "clean", "No outstanding duplicate-order deduction was found."
        if repair_errors or len(opening) != 1 or (rows and opening and opening[0]["id"] != rows[0]["id"]) or not ledger["stock_matches_ledger"] or not ledger["qtys_consistent"] or ledger["outstanding"] < 0:
            status, reason = "needs_review", "Missing or inconsistent ledger evidence requires manual review."
        elif ledger["outstanding"] > 0:
            status, reason = "candidate", "Repeated order deductions found. Evidence and the code fix must be investigated before any repair."
        snapshot = store.fingerprint(conn, product_id)
        if register:
            existing = store.get_incident(conn, store.CANONICAL_INCIDENT)
            if existing and existing["product_id"] != product_id:
                raise AgentError("The workspace is bound to a different product.")
            incident_status = "open" if status != "clean" else "resolved"
            conn.execute("INSERT INTO incidents(id,product_id,description,status) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET description=excluded.description,status=excluded.status", (store.CANONICAL_INCIDENT, product_id, reason, incident_status))
            conn.commit()
        else:
            conn.rollback()
        return {"enabled": True, "connector": "sqlite-inventory-v1", "product_id": product_id,
                "product_name": product["name"], "events": len(rows), "stock": product["stock"],
                "status": status, "reason": reason, "snapshot_hash": snapshot,
                "incident_id": store.CANONICAL_INCIDENT, "model_requests": 0,
                "model_key_configured": bool(os.environ.get("MODEL_API_KEY")),
                "runtime_configured": bool(os.environ.get("AFTERFIX_RUNTIME_DIR"))}
    finally:
        conn.close()


def _private_write(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
        out.write(content)


def install(args):
    if not args.consent:
        raise AgentError("Use --consent-schema-extension after reviewing docs/INSTALL_AGENT.md. A backup and schema extension are required.")
    database, product_id = inspect_source(args.database, args.product)
    patch = Path(args.patch).expanduser().resolve(strict=True)
    raw = patch.read_bytes()
    if not 0 < len(raw) <= 8000 or not raw.decode("utf-8").strip():
        raise AgentError("Supply a UTF-8 code fix patch of 1–8000 bytes.")
    home = Path(args.directory).expanduser().resolve()
    if home.exists():
        raise AgentError("Choose a new installation directory; existing configuration is never overwritten.")
    if home == database.parent or database.parent in home.parents or home in database.parents:
        raise AgentError("Keep the private installation directory separate from the database directory.")
    if any(c in str(database) + str(home) + product_id for c in ["\n", "\r", "\x00", ","]):
        raise AgentError("Unsupported path characters.")
    uid = os.getuid() if hasattr(os, "getuid") and os.getuid() != 0 else 10001
    gid = os.getgid() if hasattr(os, "getgid") and os.getuid() != 0 else 10001
    if not args.no_start:
        with socket.socket() as port_check:
            port_check.bind(("127.0.0.1", args.port))
        if not args.no_build:
            _build_image()
        command = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                   "--user", str(uid) + ":" + str(gid), "--mount", "type=bind,src=" + str(database.parent) + ",dst=/inventory", "afterfix-agent:local", "python", "-c",
                   "import os,sys;sys.exit(0 if os.access('/inventory',os.W_OK) and os.access(sys.argv[1],os.R_OK|os.W_OK) else 1)", "/inventory/" + database.name]
        if subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            raise AgentError("The container user cannot write the selected database directory/file. Fix its dedicated-directory permissions before installation.")
    home.mkdir(mode=0o700, parents=True)
    if os.name == "nt":
        identity = subprocess.check_output(["whoami"], text=True).strip()
        subprocess.run(["icacls", str(home), "/inheritance:r", "/grant:r", identity + ":(OI)(CI)F", "SYSTEM:(OI)(CI)F"], check=True, stdout=subprocess.DEVNULL)
    installation_id = secrets.token_hex(16)
    _private_write(home / "fix.patch", raw.decode("utf-8"))
    initialize_database(database, product_id, home / "before-install.sqlite", installation_id)
    config = {"version": 1, "database": str(database), "product_id": product_id,
              "installation_id": installation_id, "port": args.port,
              "container": "afterfix-agent-" + installation_id[:12],
              "uid": uid, "gid": gid}
    _private_write(home / "agent.json", json.dumps(config, indent=2) + "\n")
    _private_write(home / "access.env", "AFTERFIX_OPERATOR_TOKEN=" + secrets.token_urlsafe(32) + "\n")
    # Deliberately no fake credit ledger, model key or automatic provider call.
    _private_write(home / "model.env", "# Optional: MODEL_API_KEY only; never put application access keys here.\n")
    if hasattr(os, "getuid") and os.getuid() == 0:
        # Only newly created private installation files, never the source DB.
        for file in [home, *home.iterdir()]:
            os.chown(file, config["uid"], config["gid"])
    print("Connected inventory file. Verified backup and private access file saved in the installation directory.")
    if not args.no_start:
        start(home, build=False)


def _build_image():
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["docker", "build", "--target", "runtime", "-f", str(root / "deploy/Dockerfile"), "-t", "afterfix-agent:local", str(root)], check=True)


def start(home, *, build=True):
    home = Path(home).expanduser().resolve(strict=True)
    config = json.loads((home / "agent.json").read_text(encoding="utf-8"))
    image = "afterfix-agent:local"
    if build:
        _build_image()
    # Same container name makes repeated starts idempotent; no unrelated cleanup.
    found = subprocess.run(["docker", "container", "inspect", config["container"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if found.returncode == 0:
        _owned_container(config)
        subprocess.run(["docker", "start", config["container"]], check=True, stdout=subprocess.DEVNULL)
    else:
        database = Path(config["database"])
        env = {"AFTERFIX_AGENT_MODE": "1", "AFTERFIX_AGENT_ID": config["installation_id"],
               "AFTERFIX_AGENT_PRODUCT": config["product_id"], "AFTERFIX_DB": "/inventory/" + database.name,
               "AFTERFIX_AGENT_PATCH": "/agent/fix.patch", "AFTERFIX_AGENT_PATCH_SHA256": hashlib.sha256((home / "fix.patch").read_bytes()).hexdigest(),
               "AFTERFIX_ALLOW_RESET": "0", "AFTERFIX_ENABLE_SCENARIO_LAB": "0", "AFTERFIX_PUBLIC_VIEWER": "0"}
        command = ["docker", "run", "-d", "--name", config["container"], "--label", "cc.sklab.afterfix.agent=" + config["installation_id"],
                   "--user", str(config["uid"]) + ":" + str(config["gid"]), "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                   "--memory", "512m", "--pids-limit", "128", "--cpus", "1", "--tmpfs", "/tmp:size=64m,mode=1777",
                   "-p", "127.0.0.1:" + str(config["port"]) + ":8000",
                   "--mount", "type=bind,src=" + str(database.parent) + ",dst=/inventory",
                   "--mount", "type=bind,src=" + str(home) + ",dst=/agent,readonly",
                   "--env-file", str(home / "access.env")]
        model_lines = [s for s in (home / "model.env").read_text(encoding="utf-8").splitlines() if s.strip() and not s.lstrip().startswith("#")]
        if any(not s.startswith("MODEL_API_KEY=") for s in model_lines) or len(model_lines) > 1:
            raise AgentError("model.env accepts only one server-side MODEL_API_KEY setting.")
        if model_lines:
            runtime = home / "runtime"
            if not all((runtime / f).is_file() for f in ["budget-state.json", "credit-state.json", "runner.lock"]):
                raise AgentError("Provision the existing verified shared runtime ledger before enabling the model.")
            command += ["--env-file", str(home / "model.env"), "--mount", "type=bind,src=" + str(runtime) + ",dst=/runtime"]
            env["AFTERFIX_RUNTIME_DIR"] = "/runtime"
        for name, value in env.items():
            command += ["--env", name + "=" + value]
        command += [image, "uvicorn", "app.agent_site:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
    for _ in range(30):
        state = json.loads(subprocess.check_output(["docker", "inspect", "--format", "{{json .State}}", config["container"]], text=True))
        if not state.get("Running"):
            raise AgentError("The agent stopped during startup. Check database-directory write permission for the configured container user and supported schema.")
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("http://127.0.0.1:" + str(config["port"]) + "/health", timeout=1) as response:
                if response.status == 200 and json.loads(response.read(1024)) == {"ok": True}:
                    break
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(1)
    else:
        raise AgentError("The agent did not become healthy. Check the local installation before continuing.")
    print("Agent started on http://127.0.0.1:" + str(config["port"]) + ". Use the private access.env operator key inside Operator access. No model calls run on startup.")


def _owned_container(config):
    labels = json.loads(subprocess.check_output(["docker", "inspect", "--format", "{{json .Config.Labels}}", config["container"]], text=True))
    if not isinstance(labels, dict) or labels.get("cc.sklab.afterfix.agent") != config["installation_id"]:
        raise AgentError("The container name is occupied by a different installation; no container was changed.")


def stop(home, *, remove=False):
    config = json.loads((Path(home).expanduser().resolve(strict=True) / "agent.json").read_text(encoding="utf-8"))
    _owned_container(config)
    subprocess.run(["docker", "stop", config["container"]], check=True, stdout=subprocess.DEVNULL)
    if remove:
        subprocess.run(["docker", "rm", config["container"]], check=True, stdout=subprocess.DEVNULL)
    print("Stopped only this agent. Inventory, backup and private configuration retained.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install/start the bounded local SQLite recovery agent.")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("install")
    setup.add_argument("--database")
    setup.add_argument("--patch")
    setup.add_argument("--product")
    setup.add_argument("--directory")
    setup.add_argument("--port", type=int, default=18157)
    setup.add_argument("--consent-schema-extension", dest="consent", action="store_true")
    setup.add_argument("--no-start", action="store_true")
    setup.add_argument("--no-build", action="store_true")
    run = commands.add_parser("start")
    run.add_argument("--directory", required=True)
    run.add_argument("--no-build", action="store_true")
    halt = commands.add_parser("stop")
    halt.add_argument("--directory", required=True)
    halt.add_argument("--remove", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            interactive = sys.stdin.isatty()
            if not args.database and interactive:
                args.database = input("Inventory SQLite file: ").strip()
            if not args.patch and interactive:
                args.patch = input("Code fix patch file (UTF-8): ").strip()
            if not args.directory and interactive:
                args.directory = input("Private installation directory (new, separate from database): ").strip()
            if not args.product and interactive:
                args.product = input("Product ID (blank only for a one-product database): ").strip() or None
            if not args.consent and interactive:
                print("Installation backs up the file, adds AfterFix metadata tables and two event columns. Stock/event values stay unchanged. Apps must use named INSERT columns. Read docs/INSTALL_AGENT.md first.")
                args.consent = input("Do you consent to this schema extension? Type INSTALL: ").strip() == "INSTALL"
            if not args.database or not args.patch or not args.directory:
                raise AgentError("Supply --database, --patch and --directory, or run install interactively in a terminal.")
            if not 1024 <= args.port <= 65535:
                raise AgentError("Choose a local port between 1024 and 65535.")
            install(args)
        elif args.command == "start":
            start(args.directory, build=not args.no_build)
        else:
            stop(args.directory, remove=args.remove)
    except AgentError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError):
        print("Agent setup could not complete. Check schema consent, supported inventory data, private directory, Docker and file permissions; existing data was not automatically restored. See docs/INSTALL_AGENT.md.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
