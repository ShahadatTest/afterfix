"""Portable guarded runtime transport for the official Meta Responses API.

Trusted server-side code constructs this with an existing runtime directory
shared with the coding ledger. It never creates/initializes/resets that
ledger, never reads credential files, and persists a conservative reservation
before any network activity. Every network call reloads and revalidates the
shared budget and credit state under the already-held locks, so freshness,
reserve, cap and unresolved-spend checks apply per call rather than only at
context entry.
"""
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from decimal import Decimal, InvalidOperation

from app.meta_client import MODEL
from app.tls_context import trusted_tls_context

OFFICIAL_URL = "https://api.meta.ai/v1/responses"
INPUT_RATE = 1.25e-6
OUTPUT_RATE = 4.25e-6
_INPUT_RATE_D = Decimal("0.00000125")
_OUTPUT_RATE_D = Decimal("0.00000425")
TOTAL_CAP_USD = 100.0
RUN_CAP_USD = 0.25
RESERVE_USD = 50.0
MAX_REQUESTS_PER_RUN = 6
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_SERIALIZED_BYTES = 400000
CREDIT_MAX_AGE_SECONDS = 86400
_EPS = Decimal("0.000000000001")
_ALLOWED_ROOT_KEYS = {
    "model", "input", "store", "reasoning", "max_output_tokens", "tools",
    "tool_choice", "parallel_tool_calls", "text",
}
_ALLOWED_TOOLS = {"get_patch", "read_events"}
_MEDIA_TYPES = {
    "input_image", "image", "input_audio", "audio", "input_video", "video",
    "input_file", "file", "image_url", "file_data", "document",
}
_SERVER_TYPES = {"web_search", "code_interpreter", "file_search", "computer_use", "mcp"}
_MEDIA_KEYS = {"image_url", "file_id", "file_data", "audio_data", "video_url", "file"}
_PROHIBITED_KEYS = _MEDIA_KEYS | {
    "previous_response_id", "contributor", "contributor_mode", "training",
}
_UNRESOLVED = {
    "reserved", "pending", "unknown", "unknown_usage", "in_flight", "inflight",
    "started", "processing", "active", "running", "open", "needs_review",
    "violation", "blocked",
}
_TERMINAL = {
    "settled", "completed", "complete", "finalized", "finalised", "final",
    "charged", "done", "succeeded", "success", "paid", "recorded", "closed", "ok",
}
_USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens")

_PROCESS_LOCKS = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


class RuntimeBudgetError(Exception):
    """Sanitized guarded-runtime failure (no paths, keys or upstream text)."""


class RuntimeLockBusy(RuntimeBudgetError):
    """The shared runner lock is already held."""


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_nonneg_number(value):
    """True for a finite, non-negative int/float; large ints never overflow."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    if isinstance(value, float):
        return math.isfinite(value) and value >= 0
    return False


def _is_money(value):
    return _is_nonneg_number(value)


def _dec(value):
    """Exact Decimal for a validated non-negative int/float; never overflows."""
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    return Decimal(str(value))


def _process_lock(key):
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PROCESS_LOCKS[key] = lock
        return lock


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_opener(request, timeout):
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=trusted_tls_context()))
    return opener.open(request, timeout=timeout)


class RuntimeBudgetTransport:
    """Context-managed callable transport compatible with MetaClient."""

    def __init__(self, runtime_dir, opener=None, clock=None, timeout=60):
        if not _is_nonneg_number(timeout) or not (1 <= float(timeout) <= 180):
            raise RuntimeBudgetError("runtime unavailable")
        self._dir_value = runtime_dir
        self._opener = opener or _default_opener
        self._clock = clock or time.time
        self._timeout = float(timeout)
        self._call_lock = threading.Lock()
        self._entered = False
        self._blocked = False
        self._lock_file = None
        self._process_lock = None
        self._dir = None
        self._budget_path = None
        self._credit_path = None
        self._budget = None
        self._credit = None
        self._run_id = None
        self._run_record = None
        self._calls = 0
        self._api_key = None

    # ---------- validation helpers ----------
    def _now(self):
        try:
            now = float(self._clock())
        except Exception:
            raise RuntimeBudgetError("runtime unavailable") from None
        if not math.isfinite(now) or now < 0:
            raise RuntimeBudgetError("runtime unavailable")
        return now

    @staticmethod
    def _read_json(path):
        try:
            if not os.path.isfile(path) or os.path.getsize(path) > MAX_STATE_BYTES:
                raise RuntimeBudgetError("runtime unavailable")
            with open(path, "r", encoding="utf-8") as handle:
                raw = handle.read(MAX_STATE_BYTES + 1)
        except RuntimeBudgetError:
            raise
        except Exception:
            raise RuntimeBudgetError("runtime unavailable") from None
        if len(raw.encode("utf-8")) > MAX_STATE_BYTES:
            raise RuntimeBudgetError("runtime unavailable")
        try:
            value = json.loads(raw)
        except Exception:
            raise RuntimeBudgetError("runtime unavailable") from None
        if not isinstance(value, dict):
            raise RuntimeBudgetError("runtime unavailable")
        return value

    @staticmethod
    def _validate_usage_dict(usage, strict_top=False):
        if not isinstance(usage, dict):
            raise RuntimeBudgetError("runtime unavailable")
        for key in _USAGE_KEYS:
            if key in usage and (not _is_int(usage[key]) or usage[key] < 0):
                raise RuntimeBudgetError("runtime unavailable")
        if strict_top:
            inp, out = usage.get("input_tokens"), usage.get("output_tokens")
            if not _is_int(inp) or not _is_int(out) or inp < 0 or out < 0:
                raise RuntimeBudgetError("runtime unavailable")
            total = usage.get("total_tokens")
            if total is not None and (not _is_int(total) or total < inp + out):
                raise RuntimeBudgetError("runtime unavailable")
            return inp, out, total
        return None

    def _validate_budget(self, budget):
        """Validate the ledger; returns the charged total as a Decimal.

        Unresolved *request* statuses are authoritative and block. Historical
        *run* labels are not unresolved spend: a collaborating process is
        excluded by the held runner lock, and orphan/settled run labels such
        as 'starting', 'needs_review', 'staged_for_review' or
        'live_evaluation_finished' must not reject otherwise settled history.
        """
        if not _is_int(budget.get("version")) or budget.get("version") != 1:
            raise RuntimeBudgetError("runtime unavailable")
        if not isinstance(budget.get("basis"), str) or not budget["basis"]:
            raise RuntimeBudgetError("runtime unavailable")
        if not _is_money(budget.get("total_cap_usd")):
            raise RuntimeBudgetError("runtime unavailable")
        requests, runs = budget.get("requests"), budget.get("runs")
        if not isinstance(requests, list) or not isinstance(runs, list):
            raise RuntimeBudgetError("runtime unavailable")
        total = Decimal(0)
        for record in requests:
            if not isinstance(record, dict):
                raise RuntimeBudgetError("runtime unavailable")
            for key in ("id", "run_id", "status"):
                if not isinstance(record.get(key), str) or not record[key]:
                    raise RuntimeBudgetError("runtime unavailable")
            for key in ("reserved_usd", "charged_usd"):
                if not _is_money(record.get(key)):
                    raise RuntimeBudgetError("runtime unavailable")
            # Historical records may omit model/rates entirely; never guess
            # or reprice them. Validate those fields only when present.
            if "model" in record and record["model"] is not None:
                if not isinstance(record["model"], str) or not record["model"]:
                    raise RuntimeBudgetError("runtime unavailable")
            for key in ("rate_in", "rate_out"):
                if key in record and record[key] is not None and not _is_money(record[key]):
                    raise RuntimeBudgetError("runtime unavailable")
            status = record["status"].strip().lower()
            if status in _UNRESOLVED or status not in _TERMINAL:
                raise RuntimeBudgetError("runtime unavailable")
            if "usage" in record and record["usage"] is not None:
                self._validate_usage_dict(record["usage"])
            total += _dec(record["charged_usd"])
        for run in runs:
            if not isinstance(run, dict):
                raise RuntimeBudgetError("runtime unavailable")
            if not isinstance(run.get("id"), str) or not run["id"]:
                raise RuntimeBudgetError("runtime unavailable")
            # Any historical run label is allowed; validate shape/types only.
            if "status" in run and run["status"] is not None:
                if not isinstance(run["status"], str) or not run["status"]:
                    raise RuntimeBudgetError("runtime unavailable")
            if "cap_usd" in run and run["cap_usd"] is not None and not _is_money(run["cap_usd"]):
                raise RuntimeBudgetError("runtime unavailable")
            for key in ("started_at", "finished_at"):
                if key in run and run[key] is not None and not _is_nonneg_number(run[key]):
                    raise RuntimeBudgetError("runtime unavailable")
            if "calls" in run and run["calls"] is not None:
                if not _is_int(run["calls"]) or run["calls"] < 0:
                    raise RuntimeBudgetError("runtime unavailable")
        return total

    def _validate_credit(self, credit, total):
        if credit.get("source") not in ("owner_report", "provider_dashboard"):
            raise RuntimeBudgetError("runtime unavailable")
        if not _is_money(credit.get("available_usd")) or not _is_money(credit.get("tracked_at_verification_usd")):
            raise RuntimeBudgetError("runtime unavailable")
        verified = credit.get("verified_at")
        if not _is_nonneg_number(verified):
            raise RuntimeBudgetError("runtime unavailable")
        if "note" in credit and credit["note"] is not None and not isinstance(credit["note"], str):
            raise RuntimeBudgetError("runtime unavailable")
        try:
            age = Decimal(str(self._now())) - _dec(verified)
        except (InvalidOperation, ValueError, OverflowError):
            raise RuntimeBudgetError("runtime unavailable") from None
        if age < 0 or age > CREDIT_MAX_AGE_SECONDS:
            raise RuntimeBudgetError("runtime unavailable")
        tracked = _dec(credit["tracked_at_verification_usd"])
        if tracked > total:
            raise RuntimeBudgetError("runtime unavailable")
        return _dec(credit["available_usd"]) - max(Decimal(0), total - tracked)

    def _ledger_total(self):
        return sum((_dec(r["charged_usd"]) for r in self._budget["requests"]), Decimal(0))

    def _available_now(self):
        tracked = _dec(self._credit["tracked_at_verification_usd"])
        return _dec(self._credit["available_usd"]) - max(Decimal(0), self._ledger_total() - tracked)

    def _reload_and_validate(self):
        """Reload budget+credit under the held locks and revalidate fully.

        Returns (charged_total, available_now) as Decimals. Restores the
        in-memory references to the freshly loaded objects, including this
        run's record; a missing run record is a hard failure.
        """
        budget = self._read_json(self._budget_path)
        total = self._validate_budget(budget)
        credit = self._read_json(self._credit_path)
        available = self._validate_credit(credit, total)
        run_record = None
        for run in budget["runs"]:
            if run.get("id") == self._run_id:
                run_record = run
                break
        if run_record is None:
            raise RuntimeBudgetError("runtime unavailable")
        self._budget, self._credit, self._run_record = budget, credit, run_record
        self._calls = run_record.get("calls") if _is_int(run_record.get("calls")) else self._calls
        return total, available

    def _save_budget(self):
        tmp = None
        try:
            tmp = os.path.join(self._dir, ".budget-state.json.tmp-%d-%s" % (os.getpid(), uuid.uuid4().hex))
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self._budget, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._budget_path)
            tmp = None
        except Exception:
            raise RuntimeBudgetError("runtime unavailable") from None
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass

    # ---------- locking / context ----------
    def _acquire_file_lock(self):
        lock_path = os.path.join(self._dir, "runner.lock")
        try:
            if not os.path.isfile(lock_path) or os.path.getsize(lock_path) < 1:
                raise RuntimeBudgetError("runtime unavailable")
            handle = open(lock_path, "r+b")
        except RuntimeBudgetError:
            raise
        except Exception:
            raise RuntimeBudgetError("runtime unavailable") from None
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.lockf(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            try:
                handle.close()
            except Exception:
                pass
            raise RuntimeLockBusy("runtime busy") from None
        self._lock_file = handle

    def _release_all(self):
        handle, self._lock_file = self._lock_file, None
        if handle is not None:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.lockf(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                handle.close()
            except Exception:
                pass
        lock, self._process_lock = self._process_lock, None
        if lock is not None:
            try:
                lock.release()
            except Exception:
                pass
        self._api_key = None
        self._entered = False

    def __enter__(self):
        if self._entered:
            raise RuntimeBudgetError("runtime unavailable")
        try:
            value = self._dir_value
            if not isinstance(value, str) or not value or not os.path.isabs(value):
                raise RuntimeBudgetError("runtime unavailable")
            self._dir = os.path.normpath(value)
            if not os.path.isdir(self._dir):
                raise RuntimeBudgetError("runtime unavailable")
            key = os.path.normcase(os.path.realpath(self._dir))
            self._process_lock = _process_lock(key)
            if not self._process_lock.acquire(blocking=False):
                self._process_lock = None
                raise RuntimeLockBusy("runtime busy")
            self._acquire_file_lock()
            self._api_key = os.environ.get("MODEL_API_KEY")
            if not isinstance(self._api_key, str) or not self._api_key:
                raise RuntimeBudgetError("runtime unavailable")
            self._budget_path = os.path.join(self._dir, "budget-state.json")
            self._credit_path = os.path.join(self._dir, "credit-state.json")
            budget = self._read_json(self._budget_path)
            total = self._validate_budget(budget)
            credit = self._read_json(self._credit_path)
            self._validate_credit(credit, total)
            self._budget, self._credit = budget, credit
            self._run_id = "runtime-" + str(uuid.uuid4())
            self._run_record = {
                "id": self._run_id, "cap_usd": RUN_CAP_USD,
                "started_at": int(self._now()), "status": "running", "calls": 0,
            }
            self._budget["runs"].append(self._run_record)
            self._save_budget()
            self._entered = True
            return self
        except BaseException:
            self._release_all()
            raise

    def __exit__(self, exc_type, exc, tb):
        save_error = None
        try:
            if self._run_record is not None:
                self._run_record["status"] = "blocked" if self._blocked else ("failed" if exc_type is not None else "completed")
                self._run_record["finished_at"] = int(self._now())
                self._run_record["calls"] = self._calls
                self._save_budget()
        except Exception:
            # Cleanup failures never mask an original exception; they are
            # reported (sanitized) only when the body itself succeeded.
            save_error = RuntimeBudgetError("runtime unavailable")
        finally:
            self._release_all()
        if save_error is not None and exc_type is None:
            raise save_error
        return False

    # ---------- payload policy ----------
    @classmethod
    def _scan(cls, obj):
        """Scan the entire payload for prohibited keys/types anywhere."""
        if isinstance(obj, dict):
            lowered = {k.lower() for k in obj.keys() if isinstance(k, str)}
            if _PROHIBITED_KEYS & lowered:
                raise RuntimeBudgetError("runtime unavailable")
            kind = obj.get("type")
            if isinstance(kind, str) and (kind in _MEDIA_TYPES or kind in _SERVER_TYPES):
                raise RuntimeBudgetError("runtime unavailable")
            for value in obj.values():
                cls._scan(value)
        elif isinstance(obj, list):
            for value in obj:
                cls._scan(value)

    @classmethod
    def _has_text(cls, obj):
        if isinstance(obj, str):
            return bool(obj.strip())
        if isinstance(obj, dict):
            if obj.get("type") in ("input_text", "text") and isinstance(obj.get("text"), str) and obj["text"]:
                return True
            return any(cls._has_text(v) for v in obj.values())
        if isinstance(obj, list):
            return any(cls._has_text(v) for v in obj)
        return False

    @staticmethod
    def _validate_text_option(text):
        """Only the strict json_schema structure emitted by MetaClient."""
        if not isinstance(text, dict) or set(text.keys()) != {"format"}:
            raise RuntimeBudgetError("runtime unavailable")
        fmt = text["format"]
        if not isinstance(fmt, dict) or fmt.get("type") != "json_schema":
            raise RuntimeBudgetError("runtime unavailable")
        if fmt.get("strict") is True:
            if not set(fmt.keys()) <= {"type", "name", "schema", "strict"}:
                raise RuntimeBudgetError("runtime unavailable")
            holder = fmt
        elif isinstance(fmt.get("json_schema"), dict):
            if not set(fmt.keys()) <= {"type", "json_schema"}:
                raise RuntimeBudgetError("runtime unavailable")
            holder = fmt["json_schema"]
            if holder.get("strict") is not True or not set(holder.keys()) <= {"name", "schema", "strict"}:
                raise RuntimeBudgetError("runtime unavailable")
        else:
            raise RuntimeBudgetError("runtime unavailable")
        if "name" in holder and (not isinstance(holder["name"], str) or not holder["name"]):
            raise RuntimeBudgetError("runtime unavailable")
        if "schema" in holder and not isinstance(holder["schema"], dict):
            raise RuntimeBudgetError("runtime unavailable")

    def _validate_payload(self, payload):
        if not isinstance(payload, dict) or not set(payload.keys()) <= _ALLOWED_ROOT_KEYS:
            raise RuntimeBudgetError("runtime unavailable")
        required = {"model", "input", "store", "reasoning", "max_output_tokens"}
        if not required <= set(payload.keys()):
            raise RuntimeBudgetError("runtime unavailable")
        if payload["model"] != MODEL or payload["store"] is not False:
            raise RuntimeBudgetError("runtime unavailable")
        if payload["reasoning"] != {"effort": "low"}:
            raise RuntimeBudgetError("runtime unavailable")
        max_out = payload["max_output_tokens"]
        if not _is_int(max_out) or not (16 <= max_out <= 2048):
            raise RuntimeBudgetError("runtime unavailable")
        # Scan the whole payload, not just input: prohibited keys cannot hide
        # inside tool, text or any other allowed top-level setting.
        self._scan(payload)
        if not self._has_text(payload["input"]):
            raise RuntimeBudgetError("runtime unavailable")
        if "tools" in payload:
            tools = payload["tools"]
            if not isinstance(tools, list) or not tools or len(tools) > 8:
                raise RuntimeBudgetError("runtime unavailable")
            for tool in tools:
                if not isinstance(tool, dict) or tool.get("type") != "function" or tool.get("name") not in _ALLOWED_TOOLS:
                    raise RuntimeBudgetError("runtime unavailable")
                if not set(tool.keys()) <= {"type", "name", "description", "parameters", "strict"}:
                    raise RuntimeBudgetError("runtime unavailable")
                if "description" in tool and not isinstance(tool["description"], str):
                    raise RuntimeBudgetError("runtime unavailable")
                if "parameters" in tool and not isinstance(tool["parameters"], dict):
                    raise RuntimeBudgetError("runtime unavailable")
                if "strict" in tool and not isinstance(tool["strict"], bool):
                    raise RuntimeBudgetError("runtime unavailable")
            if payload.get("tool_choice") != "auto" or payload.get("parallel_tool_calls") is not False:
                raise RuntimeBudgetError("runtime unavailable")
        elif "tool_choice" in payload or "parallel_tool_calls" in payload:
            raise RuntimeBudgetError("runtime unavailable")
        if "text" in payload:
            self._validate_text_option(payload["text"])
        try:
            serialized = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except Exception:
            raise RuntimeBudgetError("runtime unavailable") from None
        if len(serialized) > MAX_SERIALIZED_BYTES:
            raise RuntimeBudgetError("runtime unavailable")
        bound = (Decimal(len(serialized) + 4096) * _INPUT_RATE_D) + (Decimal(max_out) * _OUTPUT_RATE_D)
        return serialized, bound

    # ---------- call ----------
    def _mark_unknown(self, record):
        record["status"] = "unknown"
        record["charged_usd"] = record["reserved_usd"]
        record.pop("usage", None)
        self._blocked = True
        try:
            self._save_budget()
        except Exception:
            pass

    def __call__(self, payload):
        with self._call_lock:
            if not self._entered or self._blocked or self._lock_file is None:
                raise RuntimeBudgetError("runtime unavailable")
            serialized, bound = self._validate_payload(payload)
            # Freshness/reserve policy is revalidated before EACH network
            # call: reload and validate both state files under the held
            # locks against the current charged total. The run's own
            # 'running' label is not unresolved spend; request records are.
            total, available = self._reload_and_validate()
            if self._calls >= MAX_REQUESTS_PER_RUN:
                self._blocked = True
                raise RuntimeBudgetError("runtime unavailable")
            run_cost = sum((_dec(r["charged_usd"]) for r in self._budget["requests"] if r["run_id"] == self._run_id), Decimal(0))
            cap = min(_dec(TOTAL_CAP_USD), _dec(self._budget["total_cap_usd"]))
            if available - bound < _dec(RESERVE_USD) - _EPS:
                raise RuntimeBudgetError("runtime unavailable")
            if total + bound > cap + _EPS or run_cost + bound > _dec(RUN_CAP_USD) + _EPS:
                raise RuntimeBudgetError("runtime unavailable")
            record = {
                "id": "req-" + uuid.uuid4().hex, "run_id": self._run_id,
                "reserved_usd": float(bound), "charged_usd": float(bound), "status": "reserved",
                "model": MODEL, "rate_in": INPUT_RATE, "rate_out": OUTPUT_RATE,
            }
            self._budget["requests"].append(record)
            self._calls += 1
            if self._run_record is not None:
                self._run_record["calls"] = self._calls
            try:
                self._save_budget()
            except Exception:
                # Reservation could not be persisted: never touch network.
                self._blocked = True
                raise RuntimeBudgetError("runtime unavailable") from None
            request = urllib.request.Request(
                OFFICIAL_URL, data=serialized, method="POST",
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + self._api_key},
            )
            try:
                response = self._opener(request, timeout=self._timeout)
                if hasattr(response, "__enter__"):
                    with response as active:
                        status = int(getattr(active, "status", active.getcode() if hasattr(active, "getcode") else 200))
                        if status < 200 or status > 299:
                            raise RuntimeBudgetError("runtime unavailable")
                        body = active.read(MAX_RESPONSE_BYTES + 1)
                else:
                    status = int(getattr(response, "status", response.getcode() if hasattr(response, "getcode") else 200))
                    if status < 200 or status > 299:
                        raise RuntimeBudgetError("runtime unavailable")
                    body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise RuntimeBudgetError("runtime unavailable")
                parsed = json.loads(body.decode("utf-8"))
                if not isinstance(parsed, dict) or parsed.get("error"):
                    raise RuntimeBudgetError("runtime unavailable")
                usage = parsed.get("usage")
                inp, out, total_tokens = self._validate_usage_dict(usage, strict_top=True)
            except Exception:
                self._mark_unknown(record)
                raise RuntimeBudgetError("runtime unavailable") from None
            actual = (Decimal(inp) * _INPUT_RATE_D) + (Decimal(out) * _OUTPUT_RATE_D)
            clean_usage = {"input_tokens": inp, "output_tokens": out}
            if total_tokens is not None:
                clean_usage["total_tokens"] = total_tokens
            try:
                actual_float = float(actual)
                finite = math.isfinite(actual_float)
            except (OverflowError, ValueError):
                finite = False
            if not finite:
                # Usage too large to form a finite cost: outcome is unknown,
                # the full reservation is retained and future runs block.
                self._mark_unknown(record)
                raise RuntimeBudgetError("runtime unavailable") from None
            if out > payload["max_output_tokens"] or actual > bound + _EPS:
                # Valid usage that breaches the conservative bound: charge
                # the actual amount and persist a nonterminal status that
                # requires review before any future context can proceed.
                record["charged_usd"] = actual_float
                record["status"] = "needs_review"
                record["usage"] = clean_usage
                self._blocked = True
                try:
                    self._save_budget()
                except Exception:
                    # Disk still holds the unresolved reservation, which
                    # likewise blocks any future context. Stay conservative.
                    record["status"] = "reserved"
                    record["charged_usd"] = record["reserved_usd"]
                    record.pop("usage", None)
                raise RuntimeBudgetError("runtime unavailable") from None
            record["charged_usd"] = actual_float
            record["status"] = "settled"
            record["usage"] = clean_usage
            try:
                self._save_budget()
            except Exception:
                # Disk still holds the conservative reserved record; keep
                # the in-memory state equally conservative and stop.
                record["status"] = "reserved"
                record["charged_usd"] = record["reserved_usd"]
                record.pop("usage", None)
                self._blocked = True
                raise RuntimeBudgetError("runtime unavailable") from None
            return parsed
