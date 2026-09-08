"""Model catalog for the EVO homelab node's local llama.cpp fleet.

Ports and aliases mirror ``/srv/llm/docker-compose.yml`` exactly. VRAM
figures are approximate (from ``evo/README.md`` measurements), used only
for scheduling heuristics, not billing.
"""

from __future__ import annotations

from typing import Any, Dict, List

from openjarvis.core.registry import ModelRegistry
from openjarvis.core.types import ModelSpec

# Port 1240 (llm-router) is deliberately absent here: its catalog is
# dynamic (whatever ``models.ini`` lists, loaded on demand) and is
# discovered live by ``EvoFleetEngine``/``evo_router_client`` instead of
# being hardcoded.
EVO_MODELS: List[ModelSpec] = [
    ModelSpec(
        model_id="qwen3-coder-30b-a3b",
        name="Qwen3.6-35B-A3B (coder alias)",
        parameter_count_b=35.0,
        active_parameter_count_b=3.0,
        context_length=131072,
        min_vram_gb=18.0,
        supported_engines=("evo_fleet",),
        provider="alibaba",
        metadata={
            "architecture": "moe",
            "port": 1234,
            "docker_service": "llm-coder",
            "isolated": False,
            "always_on": True,
        },
    ),
    ModelSpec(
        model_id="qwen3.6-35b-a3b",
        name="Qwen3.6-35B-A3B (base alias)",
        parameter_count_b=35.0,
        active_parameter_count_b=3.0,
        context_length=131072,
        min_vram_gb=18.0,
        supported_engines=("evo_fleet",),
        provider="alibaba",
        metadata={
            "architecture": "moe",
            "port": 1234,
            "docker_service": "llm-coder",
            "isolated": False,
            "always_on": True,
        },
    ),
    ModelSpec(
        model_id="gpt-oss-120b",
        name="GPT-OSS 120B (EVO general)",
        parameter_count_b=117.0,
        active_parameter_count_b=5.1,
        context_length=524288,
        min_vram_gb=63.0,
        supported_engines=("evo_fleet",),
        provider="open-source",
        metadata={
            "architecture": "moe",
            "port": 1235,
            "docker_service": "llm-general",
            "isolated": False,
            "always_on": True,
        },
    ),
    ModelSpec(
        model_id="text-embedding-nomic-embed-text-v1.5",
        name="Nomic Embed Text v1.5 (EVO)",
        parameter_count_b=0.137,
        context_length=8192,
        min_vram_gb=1.0,
        supported_engines=("evo_fleet",),
        provider="nomic",
        metadata={
            "architecture": "embedding",
            "port": 1236,
            "docker_service": "llm-embed",
            "isolated": False,
            "always_on": True,
        },
    ),
    ModelSpec(
        model_id="qwen2.5-coder-3b",
        name="Qwen2.5 Coder 3B (EVO autocomplete)",
        parameter_count_b=3.0,
        context_length=16384,
        min_vram_gb=2.0,
        supported_engines=("evo_fleet",),
        provider="alibaba",
        metadata={
            "architecture": "dense",
            "port": 1239,
            "docker_service": "llm-autocomplete",
            "isolated": False,
            "always_on": True,
        },
    ),
    ModelSpec(
        model_id="gpt-oss-20b",
        name="GPT-OSS 20B (trading-bot, isolated)",
        parameter_count_b=20.0,
        active_parameter_count_b=3.6,
        context_length=32768,
        min_vram_gb=14.0,
        supported_engines=("evo_fleet",),
        provider="open-source",
        metadata={
            "architecture": "moe",
            "port": 1241,
            "docker_service": "llm-trading",
            # Deliberately isolated: never selected by EvoScheduler, never
            # swapped, never shared with any other consumer. See
            # docs/superpowers/specs/2026-09-06-agent-orchestrator-design.md.
            "isolated": True,
            "always_on": True,
        },
    ),
]

# Fast lookup used by EvoFleetEngine / EvoScheduler without re-scanning
# EVO_MODELS on every call.
EVO_MODEL_META: Dict[str, Dict[str, Any]] = {
    spec.model_id: dict(spec.metadata) for spec in EVO_MODELS
}

# All fixed (always-on, non-catalog) ports the EVO fleet exposes.
EVO_FIXED_PORTS: List[int] = sorted({meta["port"] for meta in EVO_MODEL_META.values()})

# The one port llama.cpp's own multi-model preset server dynamically loads
# models onto (see /srv/llm/router/models.ini + evo-router CLI).
EVO_ROUTER_PORT = 1240


def register_evo_models() -> None:
    """Populate ``ModelRegistry`` with the EVO fleet's models (idempotent)."""
    for spec in EVO_MODELS:
        if not ModelRegistry.contains(spec.model_id):
            ModelRegistry.register_value(spec.model_id, spec)


__all__ = [
    "EVO_FIXED_PORTS",
    "EVO_MODELS",
    "EVO_MODEL_META",
    "EVO_ROUTER_PORT",
    "register_evo_models",
]
