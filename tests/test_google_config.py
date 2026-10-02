"""Verifica importação local com credenciais fictícias em pasta temporária."""

import json
from pathlib import Path
import tempfile
import unittest
import toml

from configurar_google import DEFAULT_REDIRECT, read_google_file, save_configuration
from portal import canonical_login_url


class GoogleConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="google-config-tests-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_import_preserves_settings_and_cookie_secret(self):
        source = self.root / "client.json"
        source.write_text(json.dumps({"web": {"client_id": "test.apps.googleusercontent.com",
            "client_secret": "fictitious-test-only", "redirect_uris": [DEFAULT_REDIRECT]}}), encoding="utf-8")
        client_id, secret = read_google_file(source)
        target = self.root / "secrets.toml"
        target.write_text(toml.dumps({"other": {"setting": "keep"}, "auth": {"cookie_secret": "a" * 64}}), encoding="utf-8")
        save_configuration(client_id, secret, target=target)
        result = toml.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(result["auth"]["cookie_secret"], "a" * 64)
        self.assertEqual(result["other"]["setting"], "keep")
        self.assertEqual(result["auth"]["google"]["client_id"], client_id)
        self.assertEqual(result["auth"]["redirect_uri"], DEFAULT_REDIRECT)

    def test_rejects_wrong_client_or_redirect(self):
        source = self.root / "client.json"
        source.write_text(json.dumps({"installed": {"client_id": "test"}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            read_google_file(source)
        source.write_text(json.dumps({"web": {"client_id": "test", "client_secret": "test", "redirect_uris": []}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            read_google_file(source)

    def test_generates_cookie_and_does_not_write_empty_credentials(self):
        target = self.root / ".streamlit" / "secrets.toml"
        with self.assertRaises(ValueError):
            save_configuration("", "", target=target)
        self.assertFalse(target.exists())
        save_configuration("test", "fictitious-test-only", target=target)
        result = toml.loads(target.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(result["auth"]["cookie_secret"]), 32)

    def test_login_uses_same_origin_as_callback(self):
        self.assertEqual(canonical_login_url("http://127.0.0.1:8502/login", DEFAULT_REDIRECT),
                         "http://localhost:8502/login")
        self.assertIsNone(canonical_login_url("http://localhost:8502/login", DEFAULT_REDIRECT))
        self.assertIsNone(canonical_login_url("http://LOCALHOST:8502/", DEFAULT_REDIRECT))
        self.assertEqual(canonical_login_url("https://other.example/login", "https://portal.example/site/oauth2callback"),
                         "https://portal.example/site/login")


if __name__ == "__main__":
    unittest.main()
