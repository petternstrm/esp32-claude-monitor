import json
import unittest
from email.message import Message
from unittest.mock import patch

import claude_monitor_mac as monitor


class UsageHistoryTests(unittest.TestCase):
    def parse(self, samples, now=100000):
        return monitor.parse_usage_history(
            json.dumps({"version": 2, "samples": samples}).encode(), now=now
        )

    def test_percentages_and_estimated_session_reset(self):
        samples = [
            {"t": 95000000, "org": "a", "u": {"fh": 0, "sd": 12}},
            {"t": 96000000, "org": "a", "u": {"fh": 8, "sd": 13}},
            {"t": 99900000, "org": "a", "u": {"fh": 25, "sd": 15}},
        ]
        data = self.parse(samples)
        self.assertEqual(data["pct_5h"], 0.25)
        self.assertEqual(data["pct_7d"], 0.15)
        self.assertEqual(data["reset_5h"], 114000)
        self.assertEqual(data["reset_7d"], 0)

    def test_rejects_stale_or_invalid_samples(self):
        with self.assertRaisesRegex(ValueError, "out of date"):
            self.parse([{"t": 90000000, "u": {"fh": 5, "sd": 6}}])
        with self.assertRaisesRegex(ValueError, "No Claude usage samples"):
            self.parse([{"t": 99900000, "u": {"fh": 101, "sd": 6}}])

    def test_newest_organization_and_zero_session(self):
        data = self.parse([
            {"t": 99990000, "org": "old", "u": {"fh": 40, "sd": 50}},
            {"t": 100000000, "org": "new", "u": {"fh": 0, "sd": 1}},
        ])
        self.assertEqual(data["pct_5h"], 0)
        self.assertEqual(data["pct_7d"], 0.01)
        self.assertEqual(data["reset_5h"], 0)

    def test_token_is_read_only_from_desktop(self):
        with patch.object(monitor, "_load_from_desktop", return_value={
            "accessToken": "in-memory", "expiresAt": 9999999999999
        }):
            self.assertEqual(monitor.get_valid_token(), "in-memory")
        with patch.object(monitor, "_load_from_desktop", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Exact reset unavailable"):
                monitor.get_valid_token()

    def test_server_values_are_fractions_and_reset_timestamps(self):
        headers = Message()
        headers["anthropic-ratelimit-unified-5h-utilization"] = "0.42"
        headers["anthropic-ratelimit-unified-5h-reset"] = "1792000000"
        headers["anthropic-ratelimit-unified-7d-utilization"] = "0.08"
        headers["anthropic-ratelimit-unified-7d-reset"] = "1792500000"

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def read(self):
                return b"{}"

        response = Response()
        response.headers = headers
        with patch.object(monitor, "get_valid_token", return_value="test-token"), \
             patch.object(monitor.urllib.request, "urlopen", return_value=response):
            result = monitor.fetch_rate_limits()
        self.assertEqual(result["pct_5h"], 0.42)
        self.assertEqual(result["pct_7d"], 0.08)
        self.assertEqual(result["reset_5h"], 1792000000)


if __name__ == "__main__":
    unittest.main()
