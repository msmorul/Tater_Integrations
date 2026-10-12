from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

helpers = types.ModuleType("helpers")
helpers.redis_client = None
sys.modules.setdefault("helpers", helpers)

from integrations import firecrawl_search


class FakeRedis:
    def __init__(self) -> None:
        self.hashes = {}
        self.strings = {}

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hset(self, key, mapping=None, **kwargs):
        self.hashes.setdefault(key, {}).update(mapping or kwargs)

    def get(self, key):
        return self.strings.get(key)


class FakeResponse:
    def __init__(self, payload) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self, _n=None):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def firecrawl_payload(web):
    return {
        "success": True,
        "data": {
            "web": web,
            "images": [],
            "news": [],
        },
    }


class FirecrawlSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        firecrawl_search.redis_client = FakeRedis()
        firecrawl_search.save_firecrawl_search_settings(api_key="fc-test-key")

    def tearDown(self) -> None:
        firecrawl_search.redis_client = None

    def _capture(self, payload):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            captured["timeout"] = timeout
            return FakeResponse(payload)

        return captured, patch("urllib.request.urlopen", fake_urlopen)

    def test_search_maps_web_results_and_emits_v2_request(self) -> None:
        payload = firecrawl_payload(
            [
                {
                    "url": "https://www.firecrawl.dev/",
                    "title": "Firecrawl",
                    "description": "The web data API.",
                    "position": 1,
                }
            ]
        )
        captured, urlopen = self._capture(payload)

        with urlopen:
            result = firecrawl_search.integration_web_search("tater search", num_results=5)

        req = captured["req"]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.full_url, "https://api.firecrawl.dev/v2/search")
        self.assertEqual(req.headers.get("Authorization"), "Bearer fc-test-key")
        self.assertEqual(
            json.loads(req.data.decode("utf-8")),
            {"query": "tater search", "limit": 5, "sources": ["web"], "safe": True},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "firecrawl_search")
        self.assertEqual(result["count"], 1)
        self.assertEqual(
            result["results"],
            [
                {
                    "title": "Firecrawl",
                    "url": "https://www.firecrawl.dev/",
                    "snippet": "The web data API.",
                    "display_url": "www.firecrawl.dev",
                }
            ],
        )

    def test_search_uses_include_domains_for_site(self) -> None:
        captured, urlopen = self._capture(firecrawl_payload([]))

        with urlopen:
            firecrawl_search.integration_web_search("pizza", site="example.com")

        body = json.loads(captured["req"].data.decode("utf-8"))
        self.assertEqual(body["query"], "pizza")
        self.assertEqual(body["includeDomains"], ["example.com"])

    def test_search_omits_safe_when_disabled(self) -> None:
        captured, urlopen = self._capture(firecrawl_payload([]))

        with urlopen:
            firecrawl_search.integration_web_search("pizza", safe="off")

        body = json.loads(captured["req"].data.decode("utf-8"))
        self.assertNotIn("safe", body)

    def test_search_requires_api_key(self) -> None:
        firecrawl_search.save_firecrawl_search_settings(api_key="")
        with patch.dict(
            "os.environ",
            {"TATER_WEB_SEARCH_FIRECRAWL_API_KEY": "", "FIRECRAWL_API_KEY": ""},
        ):
            result = firecrawl_search.integration_web_search("tater search")

        self.assertFalse(result["ok"])
        self.assertEqual(result["provider"], "firecrawl_search")
        self.assertTrue(result["needs"])

    def test_test_action_runs_a_search(self) -> None:
        _captured, urlopen = self._capture(
            firecrawl_payload(
                [{"url": "https://example.com", "title": "Example", "description": "x"}]
            )
        )

        with urlopen:
            result = firecrawl_search.run_integration_action("test", {})

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)


if __name__ == "__main__":
    unittest.main()
