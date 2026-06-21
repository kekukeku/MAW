"""Direct API vendor endpoint auto-resolution with probe + cache."""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

VENDORS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendors.json")
ROUTES_PATH = os.path.expanduser("~/.agent-cowork/vendor_routes.json")
CACHE_TTL_DAYS = 7
PROBE_TIMEOUT = 8.0

_REGION_LABELS = {
    "international": "國際節點",
    "china": "國內節點",
    "default": "主節點",
}


class DirectResolverError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key_hash(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def load_vendors() -> dict[str, Any]:
    with open(VENDORS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_routes_cache() -> dict[str, Any]:
    if not os.path.isfile(ROUTES_PATH):
        return {"routes": {}}
    try:
        with open(ROUTES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"routes": {}}


def save_routes_cache(data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(ROUTES_PATH), exist_ok=True)
    with open(ROUTES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def clear_vendor_cache(vendor_id: str) -> None:
    cache = load_routes_cache()
    cache.get("routes", {}).pop(vendor_id, None)
    save_routes_cache(cache)


def _cache_valid(entry: dict[str, Any], api_key: str) -> bool:
    if entry.get("key_hash") != _key_hash(api_key):
        return False
    resolved_at = entry.get("resolved_at", "")
    try:
        dt = datetime.fromisoformat(resolved_at.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - dt < timedelta(days=CACHE_TTL_DAYS)
    except (ValueError, TypeError):
        return False


def _region_label(vendor_label: str, region: str) -> str:
    suffix = _REGION_LABELS.get(region, region)
    return f"{vendor_label} {suffix}"


def _parse_probe_error(status_code: int, text: str) -> str:
    if status_code in (401, 403):
        return "auth"
    lower_text = text.lower() if text else ""
    if (
        "quota" in lower_text
        or "balance" in lower_text
        or "insufficient_quota" in lower_text
        or "resource_exhausted" in lower_text
        or "out_of_balance" in lower_text
        or "exceeded your current quota" in lower_text
        or status_code == 402
    ):
        return "餘額不足或額度限制 (Quota Exceeded)"
    return f"HTTP {status_code}"


async def _probe_openai_compatible(
    base_url: str,
    api_key: str,
    probe_model: str,
) -> tuple[bool, str | None]:
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": probe_model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=payload)
        if 200 <= resp.status_code < 300:
            return True, None
        return False, _parse_probe_error(resp.status_code, resp.text)
    except httpx.RequestError as exc:
        return False, str(exc)


async def _probe_anthropic(
    base_url: str,
    api_key: str,
    probe_model: str,
) -> tuple[bool, str | None]:
    url = f"{base_url.rstrip('/')}/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {
        "model": probe_model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=payload)
        if 200 <= resp.status_code < 300:
            return True, None
        return False, _parse_probe_error(resp.status_code, resp.text)
    except httpx.RequestError as exc:
        return False, str(exc)


async def _probe_endpoint(
    endpoint: dict[str, Any],
    api_style: str,
    api_key: str,
) -> tuple[bool, str | None]:
    base_url = endpoint["base_url"]
    probe_model = endpoint["probe_model"]
    if api_style == "anthropic_messages":
        return await _probe_anthropic(base_url, api_key, probe_model)
    return await _probe_openai_compatible(base_url, api_key, probe_model)


async def resolve_vendor(
    vendor_id: str,
    api_key: str,
    *,
    force_probe: bool = False,
) -> dict[str, Any]:
    """Resolve the best endpoint for a vendor key. Uses cache when valid."""
    api_key = (api_key or "").strip()
    if not api_key:
        raise DirectResolverError(f"No API key provided for vendor '{vendor_id}'.")

    vendors = load_vendors()
    vendor = vendors.get(vendor_id)
    if not vendor:
        raise DirectResolverError(f"Unknown vendor '{vendor_id}'.")

    cache = load_routes_cache()
    routes = cache.setdefault("routes", {})
    cached = routes.get(vendor_id)
    if not force_probe and cached and _cache_valid(cached, api_key):
        return {
            "ok": True,
            "vendor_id": vendor_id,
            "endpoint_id": cached["endpoint_id"],
            "base_url": cached["base_url"],
            "region": cached.get("region", "default"),
            "api_style": vendor.get("api_style", "openai_compatible"),
            "label": cached.get("label", vendor["label"]),
            "cached": True,
        }

    api_style = vendor.get("api_style", "openai_compatible")
    vendor_label = vendor.get("label", vendor_id)
    last_error = "unknown"

    for endpoint in vendor.get("endpoints", []):
        ok, err = await _probe_endpoint(endpoint, api_style, api_key)
        if ok:
            entry = {
                "endpoint_id": endpoint["id"],
                "base_url": endpoint["base_url"],
                "region": endpoint.get("region", "default"),
                "api_style": api_style,
                "resolved_at": _now_iso(),
                "key_hash": _key_hash(api_key),
                "label": _region_label(vendor_label, endpoint.get("region", "default")),
            }
            routes[vendor_id] = entry
            save_routes_cache(cache)
            return {
                "ok": True,
                "vendor_id": vendor_id,
                "endpoint_id": entry["endpoint_id"],
                "base_url": entry["base_url"],
                "region": entry["region"],
                "api_style": api_style,
                "label": entry["label"],
                "cached": False,
            }
        last_error = err or "probe failed"

    err_label = last_error
    if last_error == "auth":
        err_label = "金鑰無效或授權失敗 (auth)"

    raise DirectResolverError(
        f"{vendor_label} API Key 無法連線候選節點，請檢查 Key 是否有效、網路是否可達（{err_label}）"
    )


def get_resolved_route(vendor_id: str, api_key: str) -> dict[str, Any] | None:
    """Return cached route if key hash matches."""
    cache = load_routes_cache()
    entry = cache.get("routes", {}).get(vendor_id)
    if entry and _cache_valid(entry, api_key):
        return entry
    return None


async def resolve_all_configured_vendors(env: dict[str, str]) -> dict[str, Any]:
    """Probe all vendors that have keys set in env."""
    vendors = load_vendors()
    results: dict[str, Any] = {}
    for vendor_id, vendor in vendors.items():
        env_key = vendor.get("env_key", "")
        api_key = env.get(env_key, "").strip()
        if not api_key:
            continue
        try:
            resolved = await resolve_vendor(vendor_id, api_key)
            results[vendor_id] = {
                "ok": True,
                "region": resolved["region"],
                "endpoint_id": resolved["endpoint_id"],
                "label": resolved["label"],
            }
        except DirectResolverError as exc:
            results[vendor_id] = {"ok": False, "error": str(exc)}
    return results