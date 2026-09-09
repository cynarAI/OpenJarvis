"""Tests for the EVO fleet model catalog."""

from __future__ import annotations

from openjarvis.core.registry import ModelRegistry
from openjarvis.intelligence.evo_catalog import (
    EVO_FIXED_PORTS,
    EVO_MODEL_META,
    EVO_MODELS,
    EVO_ROUTER_PORT,
    register_evo_models,
)


class TestEvoCatalog:
    def test_trading_model_is_isolated(self) -> None:
        assert EVO_MODEL_META["gpt-oss-20b"]["isolated"] is True
        assert EVO_MODEL_META["gpt-oss-20b"]["port"] == 1241

    def test_no_other_model_is_isolated(self) -> None:
        for model_id, meta in EVO_MODEL_META.items():
            if model_id != "gpt-oss-20b":
                assert meta["isolated"] is False, model_id

    def test_router_port_not_in_fixed_ports(self) -> None:
        assert EVO_ROUTER_PORT not in EVO_FIXED_PORTS

    def test_fixed_ports_match_docker_compose(self) -> None:
        assert EVO_FIXED_PORTS == [1234, 1235, 1236, 1239, 1241]

    def test_embedding_model_flagged(self) -> None:
        meta = EVO_MODEL_META["text-embedding-nomic-embed-text-v1.5"]
        assert meta["architecture"] == "embedding"

    def test_register_evo_models_is_idempotent(self) -> None:
        register_evo_models()
        register_evo_models()  # must not raise
        for spec in EVO_MODELS:
            assert ModelRegistry.contains(spec.model_id)
            assert ModelRegistry.get(spec.model_id) is spec
