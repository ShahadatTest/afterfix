"""Shared explicit TLS trust context for product HTTPS transports."""
import datetime
import re
import ssl

_ERROR = "TLS trust configuration unavailable"
_MIN_VERSION = (2026, 10, 19)
_VERSION_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")


def _validate_version(value):
    if not isinstance(value, str) or not value.isascii() or len(value) > 10:
        raise RuntimeError(_ERROR)
    match = _VERSION_RE.fullmatch(value)
    if match is None:
        raise RuntimeError(_ERROR)
    try:
        parts = tuple(int(group) for group in match.groups())
        datetime.date(*parts)
    except Exception:
        raise RuntimeError(_ERROR) from None
    if parts < _MIN_VERSION:
        raise RuntimeError(_ERROR)


def trusted_tls_context():
    """Return a verified TLS client context using only pinned certifi."""
    try:
        import certifi

        _validate_version(getattr(certifi, "__version__", None))
        cafile = certifi.where()
        if not isinstance(cafile, str) or not cafile:
            raise RuntimeError(_ERROR)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        context.load_verify_locations(cafile=cafile)
        if not context.get_ca_certs():
            raise RuntimeError(_ERROR)
        if getattr(context, "keylog_filename", None):
            raise RuntimeError(_ERROR)
        return context
    except Exception:
        raise RuntimeError(_ERROR) from None
