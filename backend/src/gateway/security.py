from __future__ import annotations

import hashlib
import hmac
import time
from typing import Mapping


class SignatureVerificationError(ValueError):
    pass


def verify_slack_signature(
    *,
    signing_secret: str,
    raw_body: bytes,
    headers: Mapping[str, str],
    now_epoch: int | None = None,
    tolerance_seconds: int = 60 * 5,
) -> None:
    timestamp = _find_header(headers, "x-slack-request-timestamp")
    signature = _find_header(headers, "x-slack-signature")
    if not timestamp or not signature:
        raise SignatureVerificationError("Missing signature headers")

    now = int(time.time()) if now_epoch is None else now_epoch
    try:
        request_ts = int(timestamp)
    except ValueError as exc:
        raise SignatureVerificationError("Invalid timestamp") from exc

    if abs(now - request_ts) > tolerance_seconds:
        raise SignatureVerificationError("Stale request")

    basestring = f"v0:{timestamp}:".encode("utf-8") + raw_body
    expected = "v0=" + hmac.new(signing_secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise SignatureVerificationError("Signature mismatch")


def _find_header(headers: Mapping[str, str], key: str) -> str | None:
    lowered = key.lower()
    for header_key, value in headers.items():
        if header_key.lower() == lowered:
            return str(value)
    return None
