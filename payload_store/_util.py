from __future__ import annotations

import ipaddress
import time

from .config import FORBIDDEN_SEGMENT_BYTES, MAX_SEGMENT_LEN


def now_ms() -> int:
    return int(time.time() * 1000)


def segment_is_safe(segment: str) -> bool:
    if not segment or len(segment) > MAX_SEGMENT_LEN:
        return False
    raw = segment.encode("utf-8", "surrogatepass")
    return not any(b == 0 or b in FORBIDDEN_SEGMENT_BYTES for b in raw)


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost"}
