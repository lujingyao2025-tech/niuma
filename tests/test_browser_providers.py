import unittest

try:
    import requests  # noqa: F401
except ImportError:
    raise unittest.SkipTest("requests not installed")

from ophelia_assistant.morelogin import (
    AdsPowerClient,
    BitBrowserClient,
    MoreLoginClient,
    _cdp_from_data,
    _normalize_cdp,
    create_browser_provider,
)


class CdpParsingTests(unittest.TestCase):
    def test_normalize_host_port(self):
        self.assertEqual(
            _normalize_cdp("127.0.0.1:9222"),
            "http://127.0.0.1:9222",
        )
        self.assertEqual(
            _normalize_cdp("ws://127.0.0.1:9222/devtools/browser/abc"),
            "ws://127.0.0.1:9222/devtools/browser/abc",
        )

    def test_cdp_from_ws_puppeteer(self):
        data = {"ws": {"puppeteer": "ws://127.0.0.1:9222/devtools/browser/abc"}}
        self.assertEqual(
            _cdp_from_data(data),
            "ws://127.0.0.1:9222/devtools/browser/abc",
        )

    def test_cdp_from_debug_port(self):
        data = {"debugPort": 9222}
        self.assertEqual(_cdp_from_data(data), "http://127.0.0.1:9222")

    def test_cdp_from_list(self):
        data = [{"wsEndpoint": "ws://127.0.0.1:9222/devtools/browser/x"}]
        self.assertEqual(
            _cdp_from_data(data),
            "ws://127.0.0.1:9222/devtools/browser/x",
        )


class AdsPowerParsingTests(unittest.TestCase):
    def test_parse_urls_from_local_api_text(self):
        text = 'api_address="http://local.adspower.net:50325" and 127.0.0.1:50325'
        urls = AdsPowerClient._parse_urls(text)
        self.assertIn("http://local.adspower.net:50325", urls)
        self.assertIn("http://127.0.0.1:50325", urls)

    def test_parse_api_key_json(self):
        text = '{"apiKey": "abcdef123456789"}'
        self.assertEqual(AdsPowerClient._parse_api_key(text), "abcdef123456789")

    def test_parse_api_key_ini(self):
        text = "api_key=secret123456"
        self.assertEqual(AdsPowerClient._parse_api_key(text), "secret123456")


class BitBrowserParsingTests(unittest.TestCase):
    def test_success_payload_shapes(self):
        self.assertTrue(BitBrowserClient._successful({"success": True}))
        self.assertTrue(BitBrowserClient._successful({"code": 0}))
        self.assertTrue(BitBrowserClient._successful({"status": "success"}))
        self.assertFalse(BitBrowserClient._successful({"code": 1}))

    def test_profile_list_shapes(self):
        payload = {
            "success": True,
            "data": {"list": [{"id": "profile-1", "seq": 2}]},
        }
        profiles = BitBrowserClient._profile_list(payload)
        self.assertEqual(profiles[0]["id"], "profile-1")


class ProviderFactoryTests(unittest.TestCase):
    def test_factory_returns_requested_provider(self):
        class Settings:
            browser_provider = "adspower"
            adspower_url = "http://127.0.0.1:50325"
            adspower_api_key = "key123456"
            morelogin_url = "http://127.0.0.1:40000"
            bitbrowser_url = "http://127.0.0.1:54345"

        provider = create_browser_provider(Settings())
        self.assertIsInstance(provider, AdsPowerClient)

        Settings.browser_provider = "morelogin"
        self.assertIsInstance(create_browser_provider(Settings()), MoreLoginClient)

        Settings.browser_provider = "bitbrowser"
        self.assertIsInstance(create_browser_provider(Settings()), BitBrowserClient)


class AdsPowerProfileResolutionTests(unittest.TestCase):
    PROFILES = [
        {"serial_number": "998", "user_id": "k1g43fpa", "name": "邮箱"},
        {"serial_number": "1001", "user_id": "abc123", "name": "二号窗口"},
    ]

    def test_exact_serial_number_matches(self):
        params, error = AdsPowerClient._resolve_profile_params(self.PROFILES, 998)
        self.assertEqual(
            params,
            [{"serial_number": "998"}, {"user_id": "k1g43fpa"}],
        )
        self.assertEqual(error, "")

    def test_exact_user_id_matches(self):
        params, error = AdsPowerClient._resolve_profile_params(self.PROFILES, "abc123")
        self.assertEqual(
            params,
            [{"serial_number": "1001"}, {"user_id": "abc123"}],
        )
        self.assertEqual(error, "")

    def test_one_based_list_order_fallback(self):
        params, error = AdsPowerClient._resolve_profile_params(self.PROFILES, 2)
        self.assertEqual(params, [{"serial_number": "1001"}, {"user_id": "abc123"}])
        self.assertEqual(error, "")

    def test_missing_number_reports_available_serials(self):
        params, error = AdsPowerClient._resolve_profile_params(self.PROFILES, 996)
        self.assertIsNone(params)
        self.assertIn("996", error)
        self.assertIn("998", error)
        self.assertIn("1001", error)

    def test_profile_params_skip_empty_fields(self):
        params = AdsPowerClient._params_for_profile({"serial_number": "", "user_id": ""})
        self.assertEqual(params, [])


if __name__ == "__main__":
    unittest.main()
