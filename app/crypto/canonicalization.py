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

        # Serialized deterministic JSON: sorted keys, compact separators (',', ':')
        canonical_str = json.dumps(
            data,
            default=default_json_serial,
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=True
        )
        return canonical_str.encode("utf-8")
