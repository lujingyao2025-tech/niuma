import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ophelia_assistant.config import Settings
from ophelia_assistant.update_security import sha256_hex, verify_update_payload


def _sign_payload(version: str, url: str, sha256: str, private_key) -> dict:
    canonical = json.dumps(
        {"version": version, "url": url, "sha256": sha256},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    signature = base64.b64encode(private_key.sign(canonical.encode("utf-8"))).decode()
    return {
        "version": version,
        "url": url,
        "sha256": sha256,
        "signature": signature,
    }


class UpdateSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._private_key = Ed25519PrivateKey.generate()
        self._patch = mock.patch(
            "ophelia_assistant.update_security.PUBLIC_KEY_RAW",
            self._private_key.public_key().public_bytes_raw(),
        )
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()

    def test_valid_manifest_passes(self) -> None:
        payload = _sign_payload(
            "0.91.0",
            "https://example.com/NiuMaMail.exe",
            "a" * 64,
            self._private_key,
        )
        ok, reason = verify_update_payload(payload)
        self.assertTrue(ok, reason)

    def test_tampered_manifest_fails(self) -> None:
        payload = _sign_payload(
            "0.91.0",
            "https://example.com/NiuMaMail.exe",
            "a" * 64,
            self._private_key,
        )
        payload["url"] = "https://evil.example.com/NiuMaMail.exe"
        ok, _reason = verify_update_payload(payload)
        self.assertFalse(ok)

    def test_missing_fields_fail(self) -> None:
        ok, _reason = verify_update_payload({"version": "0.91.0"})
        self.assertFalse(ok)

    def test_sha256_hex(self) -> None:
        self.assertEqual(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )


class SettingsEncryptionGuardTests(unittest.TestCase):
    def test_save_refuses_plaintext_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch(
                "ophelia_assistant.config.app_data_dir",
                return_value=Path(temp),
            ), mock.patch(
                "ophelia_assistant.config._protect_secret",
                side_effect=lambda value: value,
            ):
                settings = Settings()
                settings.adspower_api_key = "secret-api-key"
                with self.assertRaisesRegex(RuntimeError, "加密"):
                    settings.save()


if __name__ == "__main__":
    unittest.main()
