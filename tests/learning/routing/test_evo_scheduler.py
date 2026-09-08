"""Tests for EvoScheduler — routing, isolation, holds, cooldown."""

from __future__ import annotations

import time

import pytest

from openjarvis.learning.routing.evo_scheduler import (
    COOLDOWN_SECONDS,
    EvoIsolatedModelError,
    EvoScheduler,
)


class _StubFleet:
    """Minimal fleet stub — EvoScheduler.select() only needs port_for()."""

    def port_for(self, model: str) -> int | None:
        return {
            "qwen3-coder-30b-a3b": 1234,
            "gpt-oss-120b": 1235,
            "qwen2.5-coder-3b": 1239,
            "gpt-oss-20b": 1241,
        }.get(model)

    def list_models(self) -> list[str]:
        return ["qwen3-coder-30b-a3b", "gpt-oss-120b", "qwen2.5-coder-3b"]


@pytest.fixture()
def scheduler() -> EvoScheduler:
    return EvoScheduler(fleet=_StubFleet())


class TestRouting:
    def test_code_query_routes_to_coder(self, scheduler: EvoScheduler) -> None:
        decision = scheduler.select("write a function to reverse a string")
        assert decision["model"] == "qwen3-coder-30b-a3b"
        assert decision["port"] == 1234

    def test_reasoning_query_routes_to_general(self, scheduler: EvoScheduler) -> None:
        decision = scheduler.select(
            "explain step by step, with deep reasoning, why the sky is blue "
            "considering Rayleigh scattering and atmospheric composition"
        )
        assert decision["model"] == "gpt-oss-120b"

    def test_never_selects_embedding_model_for_chat(
        self, scheduler: EvoScheduler
    ) -> None:
        for query in ("hi", "2+2", "what time is it"):
            decision = scheduler.select(query)
            assert decision["model"] != "text-embedding-nomic-embed-text-v1.5"

    def test_never_auto_selects_isolated_trading_model(
        self, scheduler: EvoScheduler
    ) -> None:
        for query in ("hi", "write code", "reason deeply about physics"):
            decision = scheduler.select(query)
            assert decision["model"] != "gpt-oss-20b"


class TestIsolation:
    def test_force_model_refuses_isolated_model(self, scheduler: EvoScheduler) -> None:
        with pytest.raises(EvoIsolatedModelError):
            scheduler.select("anything", force_model="gpt-oss-20b")

    def test_swap_to_router_model_refuses_isolated_model(
        self, scheduler: EvoScheduler
    ) -> None:
        with pytest.raises(EvoIsolatedModelError):
            scheduler.swap_to_router_model("gpt-oss-20b")

    def test_force_model_allows_non_isolated(self, scheduler: EvoScheduler) -> None:
        decision = scheduler.select("anything", force_model="gpt-oss-120b")
        assert decision["model"] == "gpt-oss-120b"
        assert decision["reason"] == "explicit"


class TestHoldsAndCooldown:
    def test_active_hold_blocks_swap(self, scheduler: EvoScheduler) -> None:
        scheduler.begin_hold("chan-1")
        result = scheduler.swap_to_router_model("qwen3.8-27b")
        assert result["swapped"] is False
        assert result["reason"] == "active_hold"

    def test_end_hold_releases_block(self, scheduler: EvoScheduler) -> None:
        scheduler.begin_hold("chan-1")
        scheduler.end_hold("chan-1")
        assert scheduler._has_active_hold() is False

    def test_cooldown_blocks_rapid_swap(self, scheduler: EvoScheduler) -> None:
        scheduler._state.last_swap_ts = time.monotonic()
        result = scheduler.swap_to_router_model("qwen3.8-27b")
        assert result["swapped"] is False
        assert result["reason"] == "cooldown"

    def test_cooldown_constant_is_positive(self) -> None:
        assert COOLDOWN_SECONDS > 0
