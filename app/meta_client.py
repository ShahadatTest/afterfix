"""Meta Responses client: fixed model, strict limits, sanitized errors."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

MODEL = "muse-spark-1.3"
OFFICIAL_URL = "https://api.meta.ai/v1/responses"
MAX_REQUESTS = 6
MAX_COST = 0.25
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MIN_OUT = 16
MAX_OUT = 2048
_INPUT_RATE = 1.25e-6
_OUTPUT_RATE = 4.25e-6
_MEDIA_TYPES = {
    "input_image", "image", "input_audio", "audio", "input_video", "video",
    "input_file", "file", "image_url", "file_data", "document",
}
_MEDIA_KEYS = {"image_url", "file_id", "file_data", "audio_data", "video_url", "media", "file"}


class MetaClientError(Exception):
    """Sanitized failure; never carries credentials or upstream text."""


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return (isinstance(v, (int, float)) and not isinstance(v, bool))


class MetaClient:
    def __init__(self, transport=None, model=None, base_url=None, api_key=None, timeout=60):
        if model is not None and model != MODEL:
            raise ValueError("model override not allowed")
        self.model = MODEL
        if not _is_num(timeout) or not (1 <= float(timeout) <= 300):
            raise ValueError("invalid timeout")
        self._timeout = timeout
        self._transport = transport
        self._requests = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._total_tokens = 0
        self._unknown_usage_calls = 0
        self._blocked = False
        self._estimated_cost = 0.0  # committed actual estimated cost
        self._reserved_cost = 0.0   # retained/outstanding reservations
        self._reserved_output_tokens = 0
        self._url = None
        self._api_key = None
        if transport is not None:
            if not callable(transport):
                raise ValueError("transport must be callable")
            return
        self._url, is_loopback = self._validate_url(base_url)
        if is_loopback:
            key = api_key if isinstance(api_key, str) and api_key else os.environ.get("MODEL_API_KEY")
            self._api_key = key or None
        else:
            key = os.environ.get("MODEL_API_KEY")
            if not key:
                raise MetaClientError("meta credential unavailable")
            self._api_key = key

    @staticmethod
    def _validate_url(raw):
        if raw is None:
            return OFFICIAL_URL, False
        if not isinstance(raw, str) or not raw or len(raw) > 300 or any(c.isspace() for c in raw):
            raise ValueError("invalid endpoint")
        p = urllib.parse.urlparse(raw)
        if p.query or p.fragment or p.username or p.password:
            raise ValueError("invalid endpoint")
        host = (p.hostname or "").lower()
        if p.scheme == "https" and host == "api.meta.ai":
            try:
                port = p.port
            except Exception:
                raise ValueError("invalid endpoint") from None
            if port is not None or p.path not in ("/v1", "/v1/", "/v1/responses"):
                raise ValueError("invalid endpoint")
            return OFFICIAL_URL, False
        if p.scheme == "http" and host == "127.0.0.1":
            try:
                port = p.port
            except Exception:
                raise ValueError("invalid endpoint") from None
            if port is None or not (1 <= port <= 65535) or p.path not in ("/v1", "/v1/", "/v1/responses"):
                raise ValueError("invalid endpoint")
            return f"http://127.0.0.1:{port}/v1/responses", True
        raise ValueError("invalid endpoint")

    def usage_summary(self):
        est = self._estimated_cost + self._reserved_cost
        return {
            "model": self.model,
            "requests": self._requests,
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "total_tokens": self._total_tokens,
            "estimated_cost": est,
            "estimated_cost_usd": est,
            "cost_usd": est,
            "unknown_usage_calls": self._unknown_usage_calls,
            "unknown_usage": self._unknown_usage_calls,
            "reserved_output_tokens": self._reserved_output_tokens,
            "reserved_cost": self._reserved_cost,
            "blocked": self._blocked,
        }

    @staticmethod
    def _validate_tools(tools):
        if tools is None:
            return None
        if not isinstance(tools, list) or not tools or len(tools) > 8:
            raise ValueError("invalid tools")
        names = set()
        for t in tools:
            if not isinstance(t, dict):
                raise ValueError("invalid tool")
            if t.get("type") != "function":
                raise ValueError("only local function tools allowed")
            n = t.get("name")
            if not isinstance(n, str) or not (1 <= len(n) <= 64) or n in names:
                raise ValueError("invalid tool name")
            names.add(n)
            if "description" in t and not isinstance(t["description"], str):
                raise ValueError("invalid tool description")
            if "strict" in t and not isinstance(t["strict"], bool):
                raise ValueError("invalid tool strict flag")
            params = t.get("parameters")
            if not isinstance(params, dict) or params.get("type") != "object" or not isinstance(params.get("properties"), dict):
                raise ValueError("invalid tool parameters")
        return tools

    @staticmethod
    def _validate_text_format(fmt):
        if fmt is None:
            return None
        if not isinstance(fmt, dict) or fmt.get("type") != "json_schema":
            raise ValueError("invalid text format")
        if fmt.get("strict") is not True or not isinstance(fmt.get("schema"), dict):
            raise ValueError("text format must be strict json_schema")
        if "name" in fmt and not isinstance(fmt["name"], str):
            raise ValueError("invalid text format")
        return fmt

    @classmethod
    def _scan_input(cls, obj):
        if isinstance(obj, dict):
            if "previous_response_id" in obj:
                raise ValueError("previous_response_id not allowed")
            t = obj.get("type")
            if isinstance(t, str) and t in _MEDIA_TYPES:
                raise ValueError("media/file content not allowed")
            if _MEDIA_KEYS & set(obj.keys()):
                # 'file' as a bare key only signals media payloads in content objects.
                if t is not None or "url" in obj or "data" in obj:
                    raise ValueError("media/file content not allowed")
            for v in obj.values():
                cls._scan_input(v)
        elif isinstance(obj, list):
            for v in obj:
                cls._scan_input(v)

    @classmethod
    def _validate_input(cls, input_items):
        if isinstance(input_items, str):
            if not input_items or len(input_items) > 200000:
                raise ValueError("invalid input")
            return input_items
        if not isinstance(input_items, list) or not input_items or len(input_items) > 100:
            raise ValueError("invalid input")
        cls._scan_input(input_items)
        return input_items

    def _default_transport(self, payload):
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        req = urllib.request.Request(self._url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                status = getattr(r, "status", 200)
                if not (200 <= int(status) <= 299):
                    raise MetaClientError("meta transport failed")
                body = r.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise MetaClientError("meta response too large")
            parsed = json.loads(body.decode("utf-8"))
        except MetaClientError:
            raise
        except Exception:
            raise MetaClientError("meta transport failed") from None
        if not isinstance(parsed, dict):
            raise MetaClientError("meta transport failed")
        return parsed

    def _fail_unknown(self, message):
        self._unknown_usage_calls += 1
        self._blocked = True
        # Reservation is retained (already in _reserved_cost) and never refunded.
        raise MetaClientError(message) from None

    def create_response(self, input_items, tools=None, text_format=None, max_output_tokens=2048):
        if self._blocked:
            raise MetaClientError("meta client blocked")
        if not _is_int(max_output_tokens) or not (MIN_OUT <= max_output_tokens <= MAX_OUT):
            raise ValueError("max_output_tokens must be 16..2048")
        input_items = self._validate_input(input_items)
        tools = self._validate_tools(tools)
        fmt = self._validate_text_format(text_format)
        payload = {
            "model": MODEL,
            "input": input_items,
            "store": False,
            "reasoning": {"effort": "low"},
            "max_output_tokens": max_output_tokens,
        }
        if tools is not None:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False
        if fmt is not None:
            payload["text"] = {"format": fmt}
        try:
            serialized = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except Exception:
            raise ValueError("invalid input") from None
        if len(serialized) > 400000:
            raise ValueError("input too large")
        if self._requests >= MAX_REQUESTS:
            self._blocked = True
            raise MetaClientError("meta request limit exceeded")
        reservation = (len(serialized) + 4096) * _INPUT_RATE + max_output_tokens * _OUTPUT_RATE
        if self._estimated_cost + self._reserved_cost + reservation > MAX_COST + 1e-12:
            self._blocked = True
            raise MetaClientError("meta cost limit exceeded")
        self._reserved_cost += reservation
        self._reserved_output_tokens += max_output_tokens
        self._requests += 1
        try:
            resp = self._transport(payload) if self._transport is not None else self._default_transport(payload)
        except Exception:
            self._fail_unknown("meta transport failed")
        if not isinstance(resp, dict) or "error" in resp and resp.get("error"):
            self._fail_unknown("invalid meta response")
        u = resp.get("usage")
        if not isinstance(u, dict):
            self._fail_unknown("invalid meta usage")
        inp, out, tot = u.get("input_tokens"), u.get("output_tokens"), u.get("total_tokens")
        if not _is_int(inp) or not _is_int(out) or inp < 0 or out < 0:
            self._fail_unknown("invalid meta usage")
        if tot is not None and (not _is_int(tot) or tot < 0 or tot < inp + out):
            self._fail_unknown("invalid meta usage")
        # Valid reconciliation: release reservation, charge actual estimate.
        self._reserved_cost -= reservation
        self._reserved_output_tokens -= max_output_tokens
        self._input_tokens += inp
        self._output_tokens += out
        self._total_tokens += tot if tot is not None else inp + out
        self._estimated_cost += inp * _INPUT_RATE + out * _OUTPUT_RATE
        return resp
