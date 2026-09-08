"""Tests for evo_router_client — the llm-router (:1240) + llm-general wrapper."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import httpx
import respx

from openjarvis.system import evo_router_client as erc


@respx.mock
class TestRouterStatus:
    def test_reachable_parses_models(self) -> None:
        respx.get(f"{erc.ROUTER_HOST}/models").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "qwen3.8-27b",
                            "status": {"value": "loaded"},
                            "architecture": {"input_modalities": ["text"]},
                        },
                        {
                            "id": "gemma-4-31b",
                            "status": {"value": "unloaded"},
                            "architecture": {"input_modalities": ["text", "image"]},
                        },
                    ]
                },
            )
        )
        status = erc.router_status()
        assert status["reachable"] is True
        ids = {m["id"]: m for m in status["models"]}
        assert ids["qwen3.8-27b"]["status"] == "loaded"
        assert ids["gemma-4-31b"]["vision"] is True

    def test_unreachable_returns_empty(self) -> None:
        respx.get(f"{erc.ROUTER_HOST}/models").mock(
            side_effect=httpx.ConnectError("refused")
        )
        status = erc.router_status()
        assert status == {"reachable": False, "models": []}

    def test_loaded_model_returns_none_when_idle(self) -> None:
        respx.get(f"{erc.ROUTER_HOST}/models").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        assert erc.router_loaded_model() is None

    def test_loaded_model_finds_loaded_entry(self) -> None:
        respx.get(f"{erc.ROUTER_HOST}/models").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "qwen3.8-27b",
                            "status": {"value": "loaded"},
                            "architecture": {"input_modalities": []},
                        }
                    ]
                },
            )
        )
        assert erc.router_loaded_model() == "qwen3.8-27b"


@respx.mock
class TestRouterLoadUnload:
    def test_load_posts_model(self) -> None:
        route = respx.post(f"{erc.ROUTER_HOST}/models/load").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        erc.router_load("qwen3.8-27b")
        assert route.called
        assert route.calls.last.request.content == b'{"model":"qwen3.8-27b"}'

    def test_unload_posts_model(self) -> None:
        route = respx.post(f"{erc.ROUTER_HOST}/models/unload").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        erc.router_unload("qwen3.8-27b")
        assert route.called


class TestFreeAndRestoreGeneral:
    def test_free_general_calls_docker_stop(self) -> None:
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            assert erc.free_general_for_router() is True
            mock_run.assert_called_once()
            assert mock_run.call_args.args[0] == ["docker", "stop", "llm-general"]

    def test_free_general_returns_false_on_docker_failure(self) -> None:
        with patch.object(subprocess, "run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "docker")
            assert erc.free_general_for_router() is False

    @respx.mock
    def test_restore_general_unloads_then_starts(self) -> None:
        respx.get(f"{erc.ROUTER_HOST}/models").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "qwen3.8-27b",
                            "status": {"value": "loaded"},
                            "architecture": {"input_modalities": []},
                        }
                    ]
                },
            )
        )
        unload_route = respx.post(f"{erc.ROUTER_HOST}/models/unload").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            assert erc.restore_general() is True
            assert mock_run.call_args.args[0] == ["docker", "start", "llm-general"]
        assert unload_route.called


class TestSysfsReaders:
    def test_read_vram_gb_parses_bytes_to_gib(self, tmp_path, monkeypatch) -> None:
        used = tmp_path / "used"
        total = tmp_path / "total"
        used.write_text(str(4 * 1024**3))
        total.write_text(str(64 * 1024**3))
        monkeypatch.setattr(erc, "_VRAM_USED_PATH", str(used))
        monkeypatch.setattr(erc, "_VRAM_TOTAL_PATH", str(total))
        result = erc.read_vram_gb()
        assert result == {"used_gb": 4.0, "total_gb": 64.0}

    def test_read_vram_gb_returns_none_if_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(erc, "_VRAM_USED_PATH", "/nonexistent/path")
        assert erc.read_vram_gb() is None

    def test_read_ram_gb_parses_meminfo(self, tmp_path, monkeypatch) -> None:
        meminfo = tmp_path / "meminfo"
        meminfo.write_text("MemTotal:       61000000 kB\nMemAvailable:    3000000 kB\n")
        real_open = open

        def fake_open(path, *args, **kwargs):
            if path == "/proc/meminfo":
                return real_open(meminfo, *args, **kwargs)
            return real_open(path, *args, **kwargs)

        with patch("builtins.open", fake_open):
            result = erc.read_ram_gb()
        assert result is not None
        assert round(result["total_gb"], 1) == round(61000000 / 1024**2, 1)
