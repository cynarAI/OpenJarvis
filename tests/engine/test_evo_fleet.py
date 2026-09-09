"""Tests for EvoFleetEngine — routes to whichever EVO port serves a model."""

from __future__ import annotations

import json

import httpx
import pytest

from openjarvis.core.registry import EngineRegistry
from openjarvis.core.types import Message, Role
from openjarvis.engine.evo_fleet import EvoFleetEngine


def _models_handler(model_ids: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in model_ids]})
        if request.url.path.endswith("/v1/chat/completions"):
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": f"served-by:{body['model']}"},
                            "finish_reason": "stop",
                        }
                    ],
                    "model": body["model"],
                },
            )
        return httpx.Response(404)

    return handler


@pytest.fixture()
def fleet() -> EvoFleetEngine:
    engine = EvoFleetEngine()
    # Wire each fixed-port client to a MockTransport serving one model, so
    # tests never touch the real network / real EVO hardware.
    fixtures = {
        1234: ["qwen3-coder-30b-a3b"],
        1235: ["gpt-oss-120b"],
        1236: ["text-embedding-nomic-embed-text-v1.5"],
        1239: ["qwen2.5-coder-3b"],
        1241: ["gpt-oss-20b"],
    }
    for port, models in fixtures.items():
        client = engine._port_clients[port]
        client._client.close()
        client._client = httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            transport=httpx.MockTransport(_models_handler(models)),
        )
    engine._router_client._client.close()
    engine._router_client._client = httpx.Client(
        base_url="http://127.0.0.1:1240",
        transport=httpx.MockTransport(lambda r: httpx.Response(404)),
    )
    yield engine
    engine.close()


class TestEvoFleetEngine:
    def test_registered_in_engine_registry(self) -> None:
        # The test suite's autouse _clean_registries fixture wipes
        # EngineRegistry before every test (see tests/conftest.py), so
        # module-import-time registration (which only ever runs once per
        # process, as in real `jarvis serve` usage) doesn't survive it here.
        # Re-registering explicitly matches the convention other engine
        # tests use (see tests/engine/test_openai_compat.py's `engine`
        # fixture).
        EngineRegistry.register_value("evo_fleet", EvoFleetEngine)
        assert EngineRegistry.contains("evo_fleet")
        assert EngineRegistry.get("evo_fleet") is EvoFleetEngine

    def test_list_models_aggregates_all_ports(self, fleet: EvoFleetEngine) -> None:
        models = fleet.list_models()
        assert "qwen3-coder-30b-a3b" in models
        assert "gpt-oss-120b" in models
        assert "gpt-oss-20b" in models

    def test_generate_routes_to_correct_port(self, fleet: EvoFleetEngine) -> None:
        result = fleet.generate(
            [Message(role=Role.USER, content="hi")], model="qwen3-coder-30b-a3b"
        )
        assert result["content"] == "served-by:qwen3-coder-30b-a3b"

    def test_generate_unknown_model_raises(self, fleet: EvoFleetEngine) -> None:
        with pytest.raises(ValueError, match="not found on any EVO port"):
            fleet.generate([Message(role=Role.USER, content="hi")], model="nope")

    def test_port_for_known_model(self, fleet: EvoFleetEngine) -> None:
        fleet.list_models()  # populate the map
        assert fleet.port_for("gpt-oss-120b") == 1235

    def test_is_isolated_reflects_catalog(self, fleet: EvoFleetEngine) -> None:
        assert fleet.is_isolated("gpt-oss-20b") is True
        assert fleet.is_isolated("qwen3-coder-30b-a3b") is False

    def test_health_true_when_any_port_reachable(self, fleet: EvoFleetEngine) -> None:
        assert fleet.health() is True

    def test_health_false_when_all_ports_down(self, fleet: EvoFleetEngine) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        for client in fleet._port_clients.values():
            client._client.close()
            client._client = httpx.Client(
                base_url=client._host, transport=httpx.MockTransport(refuse)
            )
        assert fleet.health() is False
