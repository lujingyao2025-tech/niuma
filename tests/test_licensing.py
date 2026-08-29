import base64
import os
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ophelia_assistant.trial import (
    check_trial,
    device_code,
    verify_authorization_code,
)


_CODE_STRUCT = struct.Struct(">B16sIII")


def _make_code(machine_hex: str, days: int, private_key) -> str:
    """Build a valid code with an ephemeral test key, mirroring the admin tool."""
    machine = bytes.fromhex(machine_hex)
    issued_at = int(time.time())
    expires_at = issued_at + int(days) * 24 * 60 * 60
    nonce = int.from_bytes(os.urandom(4), "big")
    payload = _CODE_STRUCT.pack(1, machine, issued_at, expires_at, nonce)
    signature = private_key.sign(payload)
    token = payload + signature
    raw = base64.b32encode(token).decode().rstrip("=")
    return "-".join(raw[index:index + 4] for index in range(0, len(raw), 4))


class LicensingHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_env: dict[str, str | None] = {}
        for key in (
            "NIUMA_MAIL_TRIAL_DIR",
            "NIUMA_MAIL_MACHINE_ID",
            "NIUMA_MAIL_DISABLE_REGISTRY",
        ):
            self._old_env[key] = os.environ.get(key)
        os.environ["NIUMA_MAIL_TRIAL_DIR"] = self._tmp.name
        os.environ["NIUMA_MAIL_MACHINE_ID"] = "TEST-MACHINE-123"
        os.environ["NIUMA_MAIL_DISABLE_REGISTRY"] = "1"
        self._private_key = Ed25519PrivateKey.generate()
        self._patch_key = mock.patch(
            "ophelia_assistant.trial._PUBLIC_KEY_RAW",
            self._private_key.public_key().public_bytes_raw(),
        )
        self._patch_key.start()

    def tearDown(self) -> None:
        self._patch_key.stop()
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def test_first_run_grants_trial_and_writes_marker(self) -> None:
        status = check_trial()
        self.assertTrue(status.active)
        self.assertGreater(status.remaining_seconds, 0)
        self.assertTrue(Path(self._tmp.name, "weekly_authorization.dat").exists())
        self.assertTrue(Path(self._tmp.name, "ever_installed.dat").exists())

    def test_deleting_state_does_not_reset_trial(self) -> None:
        check_trial()
        Path(self._tmp.name, "weekly_authorization.dat").unlink()
        status = check_trial()
        self.assertFalse(status.active)
        self.assertIn("清除", status.reason)

    def test_generated_code_activates_and_cannot_be_reused(self) -> None:
        code = _make_code(device_code().replace("-", ""), 7, self._private_key)
        ok, _message, status = verify_authorization_code(code)
        self.assertTrue(ok)
        self.assertTrue(status.active)
        ok_again, reuse_message, _status = verify_authorization_code(code)
        self.assertFalse(ok_again)
        self.assertIn("已经使用过", reuse_message)

    def test_long_code_accepted(self) -> None:
        code = _make_code(device_code().replace("-", ""), 360, self._private_key)
        ok, _message, status = verify_authorization_code(code)
        self.assertTrue(ok)
        self.assertGreaterEqual(
            status.remaining_seconds, 360 * 24 * 60 * 60 - 120
        )

    def test_codes_stack_without_cap(self) -> None:
        code_one = _make_code(device_code().replace("-", ""), 30, self._private_key)
        ok_one, _message, status_one = verify_authorization_code(code_one)
        self.assertTrue(ok_one)
        self.assertGreaterEqual(status_one.remaining_seconds, 30 * 86400 - 120)
        code_two = _make_code(device_code().replace("-", ""), 30, self._private_key)
        ok_two, _message, status_two = verify_authorization_code(code_two)
        self.assertTrue(ok_two)
        self.assertGreaterEqual(status_two.remaining_seconds, 60 * 86400 - 240)

    def test_code_for_other_machine_is_rejected(self) -> None:
        other_device = "-".join(
            ("11" * 16)[index:index + 4] for index in range(0, 32, 4)
        )
        code = _make_code(other_device.replace("-", ""), 7, self._private_key)
        ok, message, _status = verify_authorization_code(code)
        self.assertFalse(ok)
        self.assertIn("不匹配", message)


if __name__ == "__main__":
    unittest.main()
