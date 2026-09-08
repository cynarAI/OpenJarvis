"""Inference engine for EVO's fixed local llama.cpp fleet.

Composes one ``OpenAICompatEngine`` per fixed port (coder/general/embed/
autocomplete/trading) plus the dynamic llm-router catalog port, and routes
by model name — the same pattern ``MultiEngine`` uses one level up.
Registered under the ``evo_fleet`` key so ``discover_engines()`` (called by
``jarvis serve``, see cli/serve.py) picks it up automatically and merges it
into the top-level ``MultiEngine`` with zero changes to the server startup
code.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any, Dict, List

from openjarvis.core.registry import EngineRegistry
from openjarvis.core.types import Message
from openjarvis.engine._base import InferenceEngine
from openjarvis.engine._stubs import StreamChunk
from openjarvis.engine.openai_compat_engines import OpenAICompatEngine
from openjarvis.intelligence.evo_catalog import (
    EVO_FIXED_PORTS,
    EVO_MODEL_META,
    EVO_ROUTER_PORT,
    register_evo_models,
)
from openjarvis.system import evo_router_client

logger = logging.getLogger(__name__)


def _client_for_port(port: int) -> OpenAICompatEngine:
    return OpenAICompatEngine(host=f"http://127.0.0.1:{port}")


class EvoFleetEngine(InferenceEngine):
    """Routes to whichever EVO llama.cpp port currently serves a model."""

    engine_id = "evo_fleet"

    def __init__(self) -> None:
        register_evo_models()
        self._port_clients: Dict[int, OpenAICompatEngine] = {
            port: _client_for_port(port) for port in EVO_FIXED_PORTS
        }
        self._router_client = _client_for_port(EVO_ROUTER_PORT)
        self._model_port: Dict[str, int] = {}
        self._refresh_map()

    def _refresh_map(self) -> None:
        self._model_port.clear()
        for port, client in self._port_clients.items():
            try:
                for model_id in client.list_models():
                    self._model_port[model_id] = port
            except Exception as exc:
                logger.debug("Failed to list models on EVO port %s: %s", port, exc)
        loaded = evo_router_client.router_loaded_model()
        if loaded:
            self._model_port[loaded] = EVO_ROUTER_PORT

    def _client_for_model(self, model: str) -> OpenAICompatEngine:
        port = self._model_port.get(model)
        if port is None:
            self._refresh_map()
            port = self._model_port.get(model)
        if port is None:
            raise ValueError(
                f"Model {model!r} not found on any EVO port "
                f"(known: {', '.join(sorted(self._model_port.keys())) or '<none>'})."
            )
        if port == EVO_ROUTER_PORT:
            return self._router_client
        return self._port_clients[port]

    # -- Fleet-awareness, used by EvoScheduler -------------------------------

    def is_isolated(self, model: str) -> bool:
        return bool(EVO_MODEL_META.get(model, {}).get("isolated", False))

    def is_always_on(self, model: str) -> bool:
        return bool(EVO_MODEL_META.get(model, {}).get("always_on", False))

    def port_for(self, model: str) -> int | None:
        return self._model_port.get(model)

    # -- InferenceEngine interface -------------------------------------------

    def generate(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return self._client_for_model(model).generate(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        async for token in self._client_for_model(model).stream(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        ):
            yield token

    async def stream_full(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        **kwargs: Any,
    ) -> AsyncIterator["StreamChunk"]:
        async for chunk in self._client_for_model(model).stream_full(
            messages, model=model, **kwargs
        ):
            yield chunk

    def list_models(self) -> List[str]:
        self._refresh_map()
        return list(self._model_port.keys())

    def health(self) -> bool:
        return any(client.health() for client in self._port_clients.values())

    def close(self) -> None:
        for client in self._port_clients.values():
            client.close()
        self._router_client.close()


if not EngineRegistry.contains("evo_fleet"):
    EngineRegistry.register("evo_fleet")(EvoFleetEngine)


__all__ = ["EvoFleetEngine"]
