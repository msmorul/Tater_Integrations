from __future__ import annotations
__version__ = "1.0.0"

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from helpers import redis_client

FIRECRAWL_SEARCH_SETTINGS_KEY = "firecrawl_search_settings"
FIRECRAWL_SEARCH_API_URL = "https://api.firecrawl.dev/v2/search"
FIRECRAWL_SEARCH_DEFAULT_TIMEOUT_SECONDS = 15
FIRECRAWL_SEARCH_MAX_RESPONSE_BYTES = int(os.getenv("TATER_WEB_SEARCH_MAX_RESPONSE_BYTES", "2000000"))
FIRECRAWL_SEARCH_MAX_SNIPPET_CHARS = int(os.getenv("TATER_WEB_SEARCH_MAX_SNIPPET_CHARS", "600"))

INTEGRATION = {
    "id": "firecrawl_search",
    "name": "Firecrawl",
    "description": "Adds Firecrawl Search as a downloadable web search provider for Tater.",
    "badge": "FC",
    "order": 14,
    "capabilities": ["web_search"],
    "fields": [
        {"key": "firecrawl_api_key", "label": "Firecrawl API Key", "type": "password", "default": ""},
    ],
    "actions": [
        {"id": "test", "label": "Test Search", "status": "Runs one Firecrawl search query."},
    ],
}


def _text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="ignore").strip()
    return str(value or "").strip()


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(float(_text(value)))
    except Exception:
        parsed = int(default)
    return max(int(minimum), min(int(maximum), parsed))


def _read_hash(key: str) -> Dict[str, Any]:
    try:
        raw = redis_client.hgetall(key) or {}
    except Exception:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def read_firecrawl_search_settings() -> Dict[str, str]:
    current = _read_hash(FIRECRAWL_SEARCH_SETTINGS_KEY)
    try:
        legacy_api_key = redis_client.get("tater:web_search:firecrawl_api_key")
    except Exception:
        legacy_api_key = ""
    api_key = (
        _text(current.get("FIRECRAWL_API_KEY") or current.get("firecrawl_api_key"))
        or _text(legacy_api_key)
        or _text(os.getenv("TATER_WEB_SEARCH_FIRECRAWL_API_KEY") or os.getenv("FIRECRAWL_API_KEY"))
    )
    return {"FIRECRAWL_API_KEY": api_key}


def save_firecrawl_search_settings(*, api_key: Any = None) -> Dict[str, str]:
    current = read_firecrawl_search_settings()
    next_settings = {"FIRECRAWL_API_KEY": _text(current.get("FIRECRAWL_API_KEY") if api_key is None else api_key)}
    redis_client.hset(FIRECRAWL_SEARCH_SETTINGS_KEY, mapping=next_settings)
    return read_firecrawl_search_settings()


def read_integration_settings() -> Dict[str, Any]:
    settings = read_firecrawl_search_settings()
    return {"firecrawl_api_key": settings.get("FIRECRAWL_API_KEY", "")}


def save_integration_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    saved = save_firecrawl_search_settings(api_key=(payload or {}).get("firecrawl_api_key"))
    return {"firecrawl_api_key": saved.get("FIRECRAWL_API_KEY", "")}


def integration_status() -> Dict[str, Any]:
    configured = bool(read_firecrawl_search_settings().get("FIRECRAWL_API_KEY"))
    return {
        "configured": configured,
        "message": "Firecrawl is configured." if configured else "Firecrawl API key is required.",
    }


def _truncate_snippet(value: Any) -> str:
    snippet = _text(value)
    if len(snippet) > FIRECRAWL_SEARCH_MAX_SNIPPET_CHARS:
        snippet = snippet[:FIRECRAWL_SEARCH_MAX_SNIPPET_CHARS].rstrip() + "..."
    return snippet


def _display_url(url: str) -> str:
    parsed = urllib.parse.urlparse(_text(url))
    return parsed.netloc or _text(url)


def _success(
    *,
    query: str,
    start_index: int,
    max_results: int,
    results: List[Dict[str, Any]],
    site_val: str,
) -> Dict[str, Any]:
    return {
        "tool": "search_web",
        "ok": True,
        "provider": "firecrawl_search",
        "provider_label": "Firecrawl",
        "query": query,
        "start": start_index,
        "count": len(results),
        "num_results": max_results,
        "results": results,
        "site_filter": site_val or None,
        "search_time_sec": None,
        "total_results": None,
        "has_more": False,
        "next_start": None,
    }


def _error(message: str, *, needs: Optional[List[str]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "tool": "search_web",
        "ok": False,
        "provider": "firecrawl_search",
        "provider_label": "Firecrawl",
        "error": message,
    }
    if needs:
        out["needs"] = needs
    return out


def _read_json(req: urllib.request.Request, *, timeout_val: int) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    try:
        with urllib.request.urlopen(req, timeout=timeout_val) as resp:
            raw = resp.read(FIRECRAWL_SEARCH_MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(1000).decode("utf-8", errors="replace").strip()
        except Exception:
            body = ""
        return None, _error(f"Firecrawl request failed ({exc.code}): {body or exc}")
    except Exception as exc:
        return None, _error(f"Firecrawl search failed: {exc}")
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return None, _error("Invalid response from Firecrawl.")
    return payload if isinstance(payload, dict) else {"data": payload}, None


def _web_results(payload: Dict[str, Any]) -> List[Any]:
    data = payload.get("data")
    if isinstance(data, dict):
        web = data.get("web")
        return web if isinstance(web, list) else []
    return data if isinstance(data, list) else []


def integration_web_search(
    query: Any,
    *,
    num_results: Any = 5,
    start: Any = 1,
    site: Any = None,
    safe: Any = "active",
    country: Any = None,
    language: Any = None,
    timeout_sec: Any = FIRECRAWL_SEARCH_DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    del country, language
    q = _text(query)
    if not q:
        return _error("query is required.")
    settings = read_firecrawl_search_settings()
    if not settings.get("FIRECRAWL_API_KEY"):
        return _error(
            "Firecrawl is not configured.",
            needs=["Set Firecrawl API Key in the Firecrawl integration settings."],
        )

    max_results = _bounded_int(num_results, default=5, minimum=1, maximum=10)
    start_index = _bounded_int(start, default=1, minimum=1, maximum=91)
    timeout_val = _bounded_int(timeout_sec, default=FIRECRAWL_SEARCH_DEFAULT_TIMEOUT_SECONDS, minimum=3, maximum=60)
    safe_mode = _text(safe).lower()
    if safe_mode not in {"active", "off"}:
        safe_mode = "active"
    site_val = _text(site)

    body: Dict[str, Any] = {
        "query": q,
        "limit": max_results,
        "sources": ["web"],
    }
    if safe_mode == "active":
        body["safe"] = True
    if site_val:
        body["includeDomains"] = [site_val]

    req = urllib.request.Request(
        FIRECRAWL_SEARCH_API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Tater-AgentLab/1.0",
            "Authorization": f"Bearer {settings.get('FIRECRAWL_API_KEY') or ''}",
        },
        method="POST",
    )
    payload, error = _read_json(req, timeout_val=timeout_val)
    if error:
        return error

    results: List[Dict[str, Any]] = []
    for item in _web_results(payload):
        if not isinstance(item, dict):
            continue
        url = _text(item.get("url"))
        if not url:
            continue
        results.append(
            {
                "title": _text(item.get("title")) or url,
                "url": url,
                "snippet": _truncate_snippet(item.get("description") or item.get("snippet")),
                "display_url": _display_url(url),
            }
        )
    return _success(query=q, start_index=start_index, max_results=max_results, results=results, site_val=site_val)


def run_integration_action(action_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if _text(action_id) != "test":
        raise KeyError(f"Unsupported Firecrawl action: {action_id}")
    query = _text((payload or {}).get("query")) or "tater search test"
    result = integration_web_search(query, num_results=1, timeout_sec=FIRECRAWL_SEARCH_DEFAULT_TIMEOUT_SECONDS)
    if not result.get("ok"):
        raise RuntimeError(_text(result.get("error")) or "Firecrawl test failed.")
    return {"ok": True, "message": "Firecrawl returned a result.", "count": result.get("count", 0)}
