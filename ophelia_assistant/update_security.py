"""Signed update manifest verification (Ed25519 + SHA-256)."""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


PUBLIC_KEY_RAW = base64.b64decode(
    "bC2CFf3GNyzLHgfJXChp2/MxLYZjPWANWC2qMYpT104="
)


def verify_update_payload(payload: dict) -> tuple[bool, str]:
    """Verify an update manifest signed by the admin Ed25519 key."""
    if not isinstance(payload, dict):
        return False, "更新清单格式无效"
    version = str(payload.get("version") or "")
    url = str(payload.get("url") or "")
    sha256 = str(payload.get("sha256") or "").lower()
    signature_b64 = str(payload.get("signature") or "")
    if not (version and url and len(sha256) == 64 and signature_b64):
        return False, "更新清单缺少 version / url / sha256 / signature"
    canonical = json.dumps(
        {"version": version, "url": url, "sha256": sha256},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        signature = base64.b64decode(signature_b64, validate=True)
        Ed25519PublicKey.from_public_bytes(PUBLIC_KEY_RAW).verify(
            signature, canonical.encode("utf-8")
        )
    except (ValueError, InvalidSignature, base64.binascii.Error):
        return False, "更新签名无效"
    return True, ""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().lower()

