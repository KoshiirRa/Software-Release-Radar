from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from radar.notifications import NotificationError, notification_smoke_payload, send_discord


class NotificationSmokeTests(unittest.TestCase):
    def test_payload_is_deterministic_and_non_llm(self):
        first = notification_smoke_payload()
        second = notification_smoke_payload()
        self.assertEqual(first, second)
        self.assertEqual(first["title"], "Software Release Radar notification smoke test")
        self.assertIn("No software was installed, updated, restarted, or changed.", first["message"])
        self.assertIsNone(first["url"])

    @patch("radar.notifications.get_settings", return_value={"discord_enabled": "1"})
    @patch("radar.notifications.urllib.request.urlopen")
    def test_discord_webhook_sends_json(self, urlopen, _settings):
        urlopen.return_value.__enter__.return_value = MagicMock(status=204)
        send_discord(
            "https://discord.com/api/webhooks/123/token",
            "Release",
            "Version 2 is available",
            "https://example.com/release",
        )
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://discord.com/api/webhooks/123/token")
        self.assertIn(b'"content"', request.data)
        self.assertIn(b'"allowed_mentions"', request.data)

    @patch("radar.notifications.get_settings", return_value={"discord_enabled": "1"})
    def test_discord_rejects_non_discord_destination(self, _settings):
        with self.assertRaisesRegex(NotificationError, "valid Discord HTTPS webhook"):
            send_discord("https://example.com/api/webhooks/123/token", "Release", "Message")

    @patch("radar.notifications.get_settings", return_value={"discord_enabled": "0"})
    def test_discord_respects_global_disable(self, _settings):
        with self.assertRaisesRegex(NotificationError, "disabled"):
            send_discord("https://discord.com/api/webhooks/123/token", "Release", "Message")


if __name__ == "__main__":
    unittest.main()
