"""Thin client for EVO's swap primitives: the llm-router catalog (:1240)
and the llm-general (gpt-oss-120b) docker service it borrows VRAM from.

Mirrors ``~/.local/bin/evo-router`` exactly (same endpoints, same docker
commands) so this is a drop-in caller of already-debugged infrastructure,
not a reimplementation of it.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

ROUTER_HOST = "http://127.0.0.1:1240"
GENERAL_SERVICE = "llm-general"
_TIMEOUT = 10.0
_DOCKER_TIMEOUT = 30.0

_VRAM_USED_PATH = "/sys/class/drm/card0/device/mem_info_vram_used"
_VRAM_TOTAL_PATH = "/sys/class/drm/card0/device/mem_info_vram_total"


def router_status() -> Dict[str, Any]:
    """Return the llm-router's catalog (id, load status, vision flag)."""
    try:
        resp = httpx.get(f"{ROUTER_HOST}/models", timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        logger.warning("llm-router (:1240) unreachable: %s", exc)
        return {"reachable": False, "models": []}
    models = [
        {
            "id": m.get("id", ""),
            "status": m.get("status", {}).get("value", "unknown"),
            "vision": "image" in m.get("architecture", {}).get("input_modalities", []),
        }
        for m in data.get("data", [])
    ]
    return {"reachable": True, "models": models}


def router_loaded_model() -> Optional[str]:
    """Return the currently loaded catalog model id, or None if idle."""
    status = router_status()
    for m in status.get("models", []):
        if m.get("status") == "loaded":
            return m["id"]
    return None


def router_load(model_id: str) -> Dict[str, Any]:
    """Load *model_id* onto the llm-router (:1240). Unloads any other
    catalog model first (llama.cpp's ``--models-max 1`` enforces this
    server-side)."""
    resp = httpx.post(
        f"{ROUTER_HOST}/models/load",
        json={"model": model_id},
        timeout=120.0,  # cold load of a large GGUF can take a while
    )
    resp.raise_for_status()
    return resp.json()


def router_unload(model_id: str) -> Dict[str, Any]:
    """Unload *model_id* from the llm-router (:1240)."""
    resp = httpx.post(
        f"{ROUTER_HOST}/models/unload",
        json={"model": model_id},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def free_general_for_router() -> bool:
    """Stop llm-general (gpt-oss-120b) to free ~70GB for a catalog model.

    Equivalent to ``evo-router on``.
    """
    try:
        subprocess.run(
            ["docker", "stop", GENERAL_SERVICE],
            check=True,
            capture_output=True,
            timeout=_DOCKER_TIMEOUT,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.error("Failed to stop %s: %s", GENERAL_SERVICE, exc)
        return False


def restore_general() -> bool:
    """Unload the router's catalog model and restart llm-general.

    Equivalent to ``evo-router off``.
    """
    loaded = router_loaded_model()
    if loaded:
        try:
            router_unload(loaded)
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
        ) as exc:
            logger.warning("Failed to unload router model %r: %s", loaded, exc)
    try:
        subprocess.run(
            ["docker", "start", GENERAL_SERVICE],
            check=True,
            capture_output=True,
            timeout=_DOCKER_TIMEOUT,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.error("Failed to start %s: %s", GENERAL_SERVICE, exc)
        return False


def read_vram_gb() -> Optional[Dict[str, float]]:
    """Read live VRAM usage from sysfs (AMD Strix Halo iGPU)."""
    try:
        with open(_VRAM_USED_PATH) as f:
            used = int(f.read().strip())
        with open(_VRAM_TOTAL_PATH) as f:
            total = int(f.read().strip())
    except (OSError, ValueError) as exc:
        logger.debug("VRAM sysfs read failed: %s", exc)
        return None
    gib = 1024**3
    return {"used_gb": used / gib, "total_gb": total / gib}


def read_ram_gb() -> Optional[Dict[str, float]]:
    """Read live system RAM usage from /proc/meminfo."""
    try:
        fields: Dict[str, int] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    fields[key] = int(rest.strip().split()[0])  # kB
        total_kb = fields.get("MemTotal")
        avail_kb = fields.get("MemAvailable")
        if total_kb is None or avail_kb is None:
            return None
    except (OSError, ValueError, IndexError) as exc:
        logger.debug("RAM /proc/meminfo read failed: %s", exc)
        return None
    return {
        "total_gb": total_kb / 1024**2,
        "available_gb": avail_kb / 1024**2,
        "used_gb": (total_kb - avail_kb) / 1024**2,
    }


__all__ = [
    "free_general_for_router",
    "read_ram_gb",
    "read_vram_gb",
    "restore_general",
    "router_load",
    "router_loaded_model",
    "router_status",
    "router_unload",
]
