"""Resource-aware routing decision layer for the EVO local LLM fleet.

Sits above ``HeuristicRouter``: the heuristic still decides *which kind*
of model a query wants (code/small/large/reasoning), this module decides
whether that choice is actually safe to act on right now given EVO's real
VRAM state — with a hold to protect in-flight sessions, a cooldown to
avoid thrashing, and a hard, unconditional exclusion of the trading-bot's
isolated model.

V1 scope (deliberately not more): the four always-on EVO models
(coder/general/autocomplete/embed) require no swap at all — ``select()``
just picks among them. Loading something from the dynamic llm-router
catalog (:1240), which *does* require freeing ~70GB from llm-general, is
only ever done on an explicit request via ``swap_to_router_model()`` — the
routing signals available today (has_code/has_math/complexity) don't
carry enough intent to safely auto-trigger a swap that costs a minute and
briefly takes gpt-oss-120b away from every other consumer (sugar-rush
Staging included).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from openjarvis.core.types import StepType, Trace, TraceStep
from openjarvis.engine.evo_fleet import EvoFleetEngine
from openjarvis.intelligence.evo_catalog import (
    EVO_MODEL_META,
    EVO_ROUTER_PORT,
    register_evo_models,
)
from openjarvis.learning.routing.router import HeuristicRouter, build_routing_context
from openjarvis.system import evo_router_client

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 120.0
HOLD_SECONDS = 300.0  # safety expiry if a caller forgets to release a hold


class EvoIsolatedModelError(PermissionError):
    """Raised when anything tries to route to the trading-bot's isolated model."""


@dataclass
class _SchedulerState:
    holds: Dict[str, float] = field(default_factory=dict)
    last_swap_ts: float = 0.0


class EvoScheduler:
    """Decides which EVO model serves a query, safely."""

    def __init__(self, fleet: Optional[EvoFleetEngine] = None) -> None:
        register_evo_models()
        self._fleet = fleet or EvoFleetEngine()
        self._state = _SchedulerState()

    # -- Session holds --------------------------------------------------------

    def begin_hold(self, channel_id: str) -> None:
        """Mark *channel_id* as mid-conversation so a swap won't be started
        under it. Expires automatically after ``HOLD_SECONDS`` in case a
        caller forgets to release it (e.g. crashed mid-request)."""
        if not channel_id:
            return
        self._state.holds[channel_id] = time.monotonic() + HOLD_SECONDS

    def end_hold(self, channel_id: str) -> None:
        self._state.holds.pop(channel_id, None)

    def _has_active_hold(self) -> bool:
        now = time.monotonic()
        # Sweep expired holds as a side effect of checking.
        expired = [cid for cid, exp in self._state.holds.items() if exp <= now]
        for cid in expired:
            del self._state.holds[cid]
        return bool(self._state.holds)

    def _in_cooldown(self) -> bool:
        return (time.monotonic() - self._state.last_swap_ts) < COOLDOWN_SECONDS

    # -- Routing --------------------------------------------------------------

    def _safe_available_models(self) -> list[str]:
        """Always-on, chat-capable EVO models.

        Excludes anything isolated (trading-bot) and anything
        embedding-only: an embedding model reports a tiny parameter count,
        which would otherwise make ``HeuristicRouter``'s "low complexity →
        smallest model" rule pick it for ordinary chat — it cannot serve
        ``/v1/chat/completions`` at all.
        """
        return [
            model_id
            for model_id, meta in EVO_MODEL_META.items()
            if meta.get("always_on")
            and not meta.get("isolated")
            and meta.get("architecture") != "embedding"
        ]

    def select(
        self,
        query: str,
        *,
        channel_id: str = "",
        urgency: float = 0.5,
        force_model: Optional[str] = None,
        trace_store: Any = None,
    ) -> Dict[str, Any]:
        """Pick a model for *query*. Raises ``EvoIsolatedModelError`` if
        ``force_model`` names the trading-bot's isolated model."""
        started = time.time()
        if force_model is not None:
            if EVO_MODEL_META.get(force_model, {}).get("isolated"):
                raise EvoIsolatedModelError(
                    f"Refusing to route to isolated model {force_model!r} "
                    "(reserved for trading-bot)."
                )
            model = force_model
            reason = "explicit"
        else:
            available = self._safe_available_models()
            ctx = build_routing_context(query, urgency=urgency)
            router = HeuristicRouter(available_models=available)
            model = router.select_model(ctx)
            reason = "heuristic"

        decision = {
            "model": model,
            "port": self._fleet.port_for(model)
            or EVO_MODEL_META.get(model, {}).get("port"),
            "reason": reason,
            "swapped": False,
        }

        if trace_store is not None:
            self._log_trace(trace_store, query, decision, started)
        return decision

    def swap_to_router_model(
        self, model_id: str, *, channel_id: str = ""
    ) -> Dict[str, Any]:
        """Explicitly load *model_id* from the llm-router catalog (:1240),
        freeing llm-general first if needed. Refuses under an active hold
        or cooldown, and unconditionally refuses isolated models."""
        if EVO_MODEL_META.get(model_id, {}).get("isolated"):
            raise EvoIsolatedModelError(
                f"Refusing to swap in isolated model {model_id!r}."
            )
        if self._has_active_hold():
            return {"swapped": False, "reason": "active_hold", "model": model_id}
        if self._in_cooldown():
            return {"swapped": False, "reason": "cooldown", "model": model_id}

        already_loaded = evo_router_client.router_loaded_model()
        if already_loaded == model_id:
            return {"swapped": False, "reason": "already_loaded", "model": model_id}

        freed = evo_router_client.free_general_for_router()
        if not freed:
            return {
                "swapped": False,
                "reason": "free_general_failed",
                "model": model_id,
            }
        try:
            evo_router_client.router_load(model_id)
        except Exception as exc:
            logger.error("Failed to load %r on llm-router: %s", model_id, exc)
            evo_router_client.restore_general()
            return {
                "swapped": False,
                "reason": "load_failed",
                "model": model_id,
                "error": str(exc),
            }

        self._state.last_swap_ts = time.monotonic()
        self._fleet._model_port[model_id] = EVO_ROUTER_PORT
        return {
            "swapped": True,
            "reason": "loaded",
            "model": model_id,
            "port": EVO_ROUTER_PORT,
        }

    def release_router_model(self) -> Dict[str, Any]:
        """Unload whatever is on the router catalog and restore llm-general."""
        restored = evo_router_client.restore_general()
        self._state.last_swap_ts = time.monotonic()
        return {"restored": restored}

    # -- Status -----------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        vram = evo_router_client.read_vram_gb()
        ram = evo_router_client.read_ram_gb()
        router = evo_router_client.router_status()
        return {
            "fixed_models": self._fleet.list_models(),
            "router": router,
            "vram": vram,
            "ram": ram,
            "active_holds": len(self._state.holds),
            "cooldown_remaining_s": max(
                0.0, COOLDOWN_SECONDS - (time.monotonic() - self._state.last_swap_ts)
            ),
        }

    # -- Internal ---------------------------------------------------------

    @staticmethod
    def _log_trace(
        trace_store: Any, query: str, decision: Dict[str, Any], started: float
    ) -> None:
        try:
            trace = Trace(
                query=query,
                model=decision["model"],
                engine="evo_fleet",
                started_at=started,
                ended_at=time.time(),
            )
            trace.add_step(
                TraceStep(
                    step_type=StepType.ROUTE,
                    timestamp=started,
                    duration_seconds=time.time() - started,
                    input={"query": query},
                    output=decision,
                )
            )
            trace_store.save(trace)
        except Exception:
            logger.debug("Failed to log EvoScheduler trace", exc_info=True)


__all__ = ["EvoIsolatedModelError", "EvoScheduler"]
