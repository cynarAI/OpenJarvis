"""Tests that /v1/chat/stream opportunistically uses EvoScheduler routing
when the caller doesn't pin an explicit model."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from openjarvis.server.api_routes import include_all_routes  # noqa: E402


def _make_evo_aware_engine(known_models):
    """A mock engine that reports EVO model ids as available."""
    engine = MagicMock()
    engine.engine_id = "mock-evo"
    engine.list_models.return_value = list(known_models)

    async def mock_stream(messages, *, model="test-model", **kwargs):
        yield f"used:{model}"

    engine.stream = mock_stream
    engine.generate.return_value = {
        "content": "ok",
        "usage": {},
        "model": "test-model",
        "finish_reason": "stop",
    }
    return engine


def _make_app(engine):
    app = FastAPI()
    app.state.engine = engine
    app.state.model = "static-default-model"
    include_all_routes(app)
    return app


class TestEvoAwareWebSocketRouting:
    def test_uses_evo_scheduler_pick_when_model_available(self) -> None:
        engine = _make_evo_aware_engine(
            ["qwen3-coder-30b-a3b", "gpt-oss-120b", "qwen2.5-coder-3b"]
        )
        app = _make_app(engine)
        client = TestClient(app)
        with client.websocket_connect("/v1/chat/stream") as ws:
            ws.send_text(json.dumps({"message": "write a function to reverse a list"}))
            chunk = ws.receive_json()
            assert chunk["content"] == "used:qwen3-coder-30b-a3b"

    def test_falls_back_to_static_default_when_evo_model_not_served(self) -> None:
        # Engine only serves models EvoScheduler would never pick — the
        # membership check in _maybe_evo_model must reject the suggestion
        # and fall back to app.state.model, never crash.
        engine = _make_evo_aware_engine(["some-other-model"])
        app = _make_app(engine)
        client = TestClient(app)
        with client.websocket_connect("/v1/chat/stream") as ws:
            ws.send_text(json.dumps({"message": "hello there"}))
            chunk = ws.receive_json()
            assert chunk["content"] == "used:static-default-model"

    def test_explicit_model_always_wins_over_evo_scheduler(self) -> None:
        engine = _make_evo_aware_engine(["qwen3-coder-30b-a3b", "pinned-model"])
        app = _make_app(engine)
        client = TestClient(app)
        with client.websocket_connect("/v1/chat/stream") as ws:
            ws.send_text(json.dumps({"message": "write code", "model": "pinned-model"}))
            chunk = ws.receive_json()
            assert chunk["content"] == "used:pinned-model"
