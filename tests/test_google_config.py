"""Verifica importação local com credenciais fictícias em pasta temporária."""

import json
from pathlib import Path
import tempfile
import textwrap
import unittest
import toml
from streamlit.testing.v1 import AppTest

from configurar_google import DEFAULT_REDIRECT, read_google_file, save_configuration
from portal import canonical_login_url, login_redirect_error


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

    def test_published_login_does_not_send_users_to_a_local_address(self):
        current = "https://cpaz-test.streamlit.app/login"
        for hostname in ("localhost", "LOCALHOST.", "test.localhost", "127.0.0.1",
                         "127.0.0.2", "[::1]", "0.0.0.0", "[::]"):
            with self.subTest(hostname=hostname):
                callback = f"http://{hostname}:8502/oauth2callback"
                self.assertIsNone(canonical_login_url(current, callback))
                message = login_redirect_error(current, callback)
                self.assertIn("[auth]", message)
                self.assertIn("https://cpaz-test.streamlit.app/oauth2callback", message)

    def test_matching_public_and_local_login_configurations_are_allowed(self):
        self.assertIsNone(login_redirect_error("https://cpaz-test.streamlit.app/login",
                                              "https://cpaz-test.streamlit.app/oauth2callback"))
        self.assertIsNone(login_redirect_error("http://127.0.0.1:8502/login", DEFAULT_REDIRECT))
        self.assertIsNone(login_redirect_error(None, DEFAULT_REDIRECT))
        self.assertIsNone(login_redirect_error("https://cpaz-test.streamlit.app/login", None))

    def test_login_screen_blocks_local_callback_and_allows_public_callback(self):
        for callback, blocked in ((DEFAULT_REDIRECT, True),
                                  ("https://cpaz-test.streamlit.app/oauth2callback", False)):
            with self.subTest(callback=callback):
                script = textwrap.dedent(f"""
                    from types import SimpleNamespace
                    from unittest.mock import patch
                    import portal
                    config = {{"redirect_uri": {callback!r}, "cookie_secret": "f" * 64,
                              "google": {{"client_id": "test", "client_secret": "fictitious-test-only"}}}}
                    with patch.object(portal, "header"), patch.object(portal, "authenticated", return_value=False), patch.object(portal, "auth_configuration", return_value=config), patch.object(portal.st, "context", SimpleNamespace(url="https://cpaz-test.streamlit.app/login")):
                        portal.login()
                """)
                app = AppTest.from_string(script).run()
                self.assertFalse(app.exception)
                self.assertEqual(len(app.button), 1)
                self.assertEqual(app.button[0].disabled, blocked)
                self.assertEqual(len(app.error), int(blocked))
                self.assertFalse(app.get("link_button"))


if __name__ == "__main__":
    unittest.main()
