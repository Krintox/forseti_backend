import json
from typing import Any, Dict
from datetime import datetime, date

def default_json_serial(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if hasattr(obj, "value"):
        return obj.value
    if hasattr(obj, "dict"):
        return obj.dict()
    raise TypeError(f"Type {type(obj)} not serializable for canonicalization")


def _js_number_normalize(value: Any) -> Any:
    """
    Recursively rewrites whole-number floats as ints.

    RFC 8785 (JCS) defines canonical JSON number formatting to match
    ECMAScript's Number-to-String conversion, which never prints a trailing
    ".0" for an integral value (`(10000.0).toString() === "10000"`). Python's
    `json.dumps` does not follow that rule - it always renders a float as
    "10000.0". A signature computed over Python's rendering therefore fails to
    re-verify against any payload that has passed through a browser, because
    `JSON.stringify(JSON.parse(x))` collapses `10000.0` to `10000` the moment
    it touches a JS number. This previously made the "Verify authentic
    snapshot" button on the Quantum Audit page report VERIFICATION FAILED for
    a genuinely untampered snapshot whenever a ceiling or exposure value
    happened to be a round number - which is the common case, not the
    exception. Normalizing before signing makes the Python-signed bytes
    identical to what any standard JSON round-trip will reproduce.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _js_number_normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_js_number_normalize(v) for v in value]
    return value


class CanonicalSerializer:
    """
    Deterministic RFC 8785 JSON Canonicalizer.
    Produces unambiguous, deterministic byte sequences for cryptographic signing and hashing.
    """
    @staticmethod
    def canonical_bytes(data: Any) -> bytes:
        # If it's already bytes
        if isinstance(data, bytes):
            return data

        # If string
        if isinstance(data, str):
            return data.encode("utf-8")

        # Serialized deterministic JSON: sorted keys, compact separators (',', ':'),
        # ECMAScript-compatible number formatting (see _js_number_normalize).
        canonical_str = json.dumps(
            _js_number_normalize(data),
            default=default_json_serial,
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=True
        )
        return canonical_str.encode("utf-8")
