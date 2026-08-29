from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ophelia_assistant import config as config_module
from ophelia_assistant.config import Settings


def _fake_context(protected: bytes = b"fake-protected", plaintext: str = ""):
    import ctypes

    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    crypt32 = mock.Mock()
    crypt32.LocalFree.side_effect = AssertionError(
        "crypt32.LocalFree must not be used"
    )
    kernel32 = mock.Mock()

    def fake_protect(*args):
        blob = args[-1]._obj
        buffer = ctypes.create_string_buffer(protected)
        blob.cbData = len(protected)
        blob.pbData = ctypes.cast(
            buffer,
            ctypes.POINTER(ctypes.c_byte),
        )
        return 1

    plain_bytes = plaintext.encode("utf-16-le")

    def fake_unprotect(*args):
        blob = args[-1]._obj
        buffer = ctypes.create_string_buffer(plain_bytes)
        blob.cbData = len(plain_bytes)
        blob.pbData = ctypes.cast(
            buffer,
            ctypes.POINTER(ctypes.c_byte),
        )
        return 1

    crypt32.CryptProtectData.side_effect = fake_protect
    crypt32.CryptUnprotectData.side_effect = fake_unprotect
    return ctypes, wintypes, crypt32, kernel32, DATA_BLOB


@unittest.skipUnless(sys.platform == "win32", "DPAPI tests require Windows")
class DpapiSecretTests(unittest.TestCase):
    def test_nonempty_secret_encrypts_and_round_trips(self) -> None:
        secret = "my-ads-api-key-123"
        context = _fake_context(
            protected=b"fake-protected-bytes",
            plaintext=secret,
        )
        with mock.patch.object(
            config_module,
            "_dpapi_context",
            return_value=context,
        ):
            encrypted = config_module._protect_secret(secret)
        self.assertTrue(encrypted.startswith("enc:v1:"))
        self.assertEqual(
            encrypted,
            "enc:v1:"
            + base64.b64encode(b"fake-protected-bytes").decode("ascii"),
        )
        with mock.patch.object(
            config_module,
            "_dpapi_context",
            return_value=context,
        ):
            self.assertEqual(
                config_module._unprotect_secret(encrypted),
                secret,
            )

    def test_empty_secret_stays_empty(self) -> None:
        self.assertEqual(config_module._protect_secret(""), "")

    def test_already_encrypted_value_is_not_reencrypted(self) -> None:
        with mock.patch.object(
            config_module, "_dpapi_context"
        ) as context:
            result = config_module._protect_secret("enc:v1:already")
        context.assert_not_called()
        self.assertEqual(result, "enc:v1:already")

    def test_local_free_uses_kernel32_not_crypt32(self) -> None:
        context = _fake_context(protected=b"bytes")
        with mock.patch.object(
            config_module,
            "_dpapi_context",
            return_value=context,
        ):
            config_module._protect_secret("secret")
        _ctypes, _wintypes, crypt32, kernel32, _blob = context
        kernel32.LocalFree.assert_called_once()
        crypt32.LocalFree.assert_not_called()

    def test_dpapi_failure_raises_with_clear_error(self) -> None:
        context = _fake_context(protected=b"bytes")
        _ctypes, _wintypes, crypt32, _kernel32, _blob = context
        crypt32.CryptProtectData.side_effect = None
        crypt32.CryptProtectData.return_value = 0
        with mock.patch.object(
            config_module,
            "_dpapi_context",
            return_value=context,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "无法加密 API Key",
            ):
                config_module._protect_secret("secret")


class SettingsSaveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._patch = mock.patch(
            "ophelia_assistant.config.app_data_dir",
            return_value=Path(self._temp.name),
        )
        self._patch.start()
        self.path = Path(self._temp.name) / "settings.json"

    def tearDown(self) -> None:
        self._patch.stop()
        self._temp.cleanup()

    def test_empty_api_key_saves_normally(self) -> None:
        settings = Settings()
        settings.adspower_api_key = ""
        settings.save()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["adspower_api_key"], "")
        self.assertFalse(Path(str(self.path) + ".tmp").exists())

    def test_encryption_failure_preserves_original_file(self) -> None:
        self.path.write_text(
            json.dumps({"sender_name": "Original"}),
            encoding="utf-8",
        )
        settings = Settings()
        settings.sender_name = "Changed"
        settings.adspower_api_key = "secret"
        settings.mark_api_key_dirty()
        with mock.patch.object(
            config_module,
            "_protect_secret",
            side_effect=RuntimeError("DPAPI failed"),
        ), mock.patch.object(
            config_module.sys,
            "platform",
            "win32",
        ):
            with self.assertRaises(RuntimeError):
                settings.save()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["sender_name"], "Original")

    def test_already_encrypted_value_is_saved_as_is(self) -> None:
        settings = Settings()
        settings.adspower_api_key = "enc:v1:existing"
        settings._persisted_api_key = "enc:v1:existing"
        with mock.patch.object(
            config_module,
            "_protect_secret",
            wraps=config_module._protect_secret,
        ) as protect:
            settings.save()
        protect.assert_not_called()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["adspower_api_key"], "enc:v1:existing")

    def test_nonempty_api_key_saves_encrypted_and_loads_plaintext(self) -> None:
        settings = Settings()
        settings.adspower_api_key = "plain-key"
        settings.mark_api_key_dirty()
        with mock.patch.object(
            config_module,
            "_protect_secret",
            return_value="enc:v1:stored",
        ), mock.patch.object(
            config_module,
            "_unprotect_secret",
            return_value="plain-key",
        ), mock.patch.object(
            config_module.sys,
            "platform",
            "win32",
        ):
            settings.save()
            loaded = Settings.load()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["adspower_api_key"], "enc:v1:stored")
        self.assertEqual(loaded.adspower_api_key, "plain-key")


if __name__ == "__main__":
    unittest.main()
