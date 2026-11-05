from __future__ import annotations

from typing import Callable, Mapping


class DiscordSignatureVerificationError(ValueError):
    pass


def verify_discord_signature(
    *,
    public_key: str,
    raw_body: bytes,
    headers: Mapping[str, str],
    verifier: Callable[[str, bytes, bytes], None] | None = None,
) -> None:
    signature_hex = _find_header(headers, "x-signature-ed25519")
    timestamp = _find_header(headers, "x-signature-timestamp")
    if not signature_hex or not timestamp:
        raise DiscordSignatureVerificationError("Missing signature headers")

    try:
        signature_bytes = bytes.fromhex(signature_hex)
    except ValueError as exc:
        raise DiscordSignatureVerificationError("Invalid signature format") from exc

    message = timestamp.encode("utf-8") + raw_body
    verify_impl = verifier or _verify_with_nacl
    try:
        verify_impl(public_key, message, signature_bytes)
    except Exception as exc:
        raise DiscordSignatureVerificationError("Signature mismatch") from exc


def _verify_with_nacl(public_key: str, message: bytes, signature_bytes: bytes) -> None:
    try:
        from nacl.signing import VerifyKey
    except ImportError as exc:
        raise DiscordSignatureVerificationError("PyNaCl is required for Discord signature verification") from exc

    verify_key = VerifyKey(bytes.fromhex(public_key))
    verify_key.verify(message, signature_bytes)


def _find_header(headers: Mapping[str, str], key: str) -> str | None:
    target = key.lower()
    for header_key, value in headers.items():
        if header_key.lower() == target:
            return str(value)
    return None
