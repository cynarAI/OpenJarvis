"""Tests for the image_generate tool.

The tool defaults to a local diffusion model (Qwen-Image, invoked via a
governor shell script through ``subprocess.run``) and falls back to OpenAI
DALL-E when ``provider="openai"`` is passed explicitly. Local-path tests
mock ``subprocess.run`` so the suite never shells out to the real,
multi-minute governor script.
"""

from __future__ import annotations

import builtins
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from openjarvis.tools.image_tool import ImageGenerateTool

# ---------------------------------------------------------------------------
# Spec / dispatch
# ---------------------------------------------------------------------------


class TestImageGenerateToolSpec:
    def test_spec(self):
        tool = ImageGenerateTool()
        assert tool.spec.name == "image_generate"
        assert tool.spec.category == "media"
        assert "prompt" in tool.spec.parameters["properties"]
        assert "prompt" in tool.spec.parameters["required"]

    def test_tool_id(self):
        tool = ImageGenerateTool()
        assert tool.tool_id == "image_generate"

    def test_is_local(self):
        # The local (default) provider never leaves the host; the openai
        # provider does, but is opt-in via an explicit `provider` param.
        assert ImageGenerateTool().is_local is True

    def test_no_prompt(self):
        tool = ImageGenerateTool()
        result = tool.execute(prompt="")
        assert result.success is False
        assert "No prompt" in result.content

    def test_no_prompt_param(self):
        tool = ImageGenerateTool()
        result = tool.execute()
        assert result.success is False
        assert "No prompt" in result.content

    def test_unsupported_provider(self):
        tool = ImageGenerateTool()
        result = tool.execute(prompt="a cat", provider="midjourney")
        assert result.success is False
        assert "Unsupported provider" in result.content

    def test_to_openai_function(self):
        tool = ImageGenerateTool()
        fn = tool.to_openai_function()
        assert fn["type"] == "function"
        assert fn["function"]["name"] == "image_generate"


# ---------------------------------------------------------------------------
# Local provider (default) — subprocess.run is always mocked
# ---------------------------------------------------------------------------


class TestImageGenerateToolLocal:
    def _patch_governor(self, monkeypatch, tmp_path, exists: bool = True):
        governor = tmp_path / "governor.sh"
        if exists:
            governor.write_text("#!/bin/sh\n")
            governor.chmod(0o755)
        monkeypatch.setattr(
            "openjarvis.tools.image_tool._LOCAL_GOVERNOR", str(governor)
        )
        monkeypatch.setattr(
            "openjarvis.tools.image_tool._MEDIA_IMAGES_DIR", tmp_path / "images"
        )
        return governor

    def test_governor_missing(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path, exists=False)
        result = ImageGenerateTool().execute(prompt="a cat")
        assert result.success is False
        assert "not set up" in result.content

    def test_successful_generation(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path)

        def _fake_run(args, **kwargs):
            Path(args[-1]).write_bytes(b"\x89PNG fake")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("openjarvis.tools.image_tool.subprocess.run", side_effect=_fake_run):
            result = ImageGenerateTool().execute(prompt="a cat on a mat")

        assert result.success is True
        assert result.content.startswith("![a cat on a mat](/jarvis-media/images/")
        assert result.metadata["provider"] == "local"
        assert result.metadata["size"] == "1024x1024"
        assert result.metadata["url"].startswith("/jarvis-media/images/")
        assert Path(result.metadata["path"]).exists()

    def test_invalid_size_falls_back_to_default(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path)
        captured: dict[str, str] = {}

        def _fake_run(args, **kwargs):
            captured["size"] = args[2]
            Path(args[-1]).write_bytes(b"fake")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("openjarvis.tools.image_tool.subprocess.run", side_effect=_fake_run):
            result = ImageGenerateTool().execute(prompt="a cat", size="not-a-size")

        assert result.success is True
        assert captured["size"] == "1024x1024"
        assert result.metadata["size"] == "1024x1024"

    def test_custom_size_passed_through(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path)
        captured: dict[str, str] = {}

        def _fake_run(args, **kwargs):
            captured["size"] = args[2]
            Path(args[-1]).write_bytes(b"fake")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("openjarvis.tools.image_tool.subprocess.run", side_effect=_fake_run):
            result = ImageGenerateTool().execute(prompt="a cat", size="1024x576")

        assert result.success is True
        assert captured["size"] == "1024x576"

    def test_timeout(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path)

        with patch(
            "openjarvis.tools.image_tool.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="governor", timeout=900),
        ):
            result = ImageGenerateTool().execute(prompt="a cat")

        assert result.success is False
        assert "timed out" in result.content

    def test_subprocess_raises(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path)

        with patch(
            "openjarvis.tools.image_tool.subprocess.run",
            side_effect=OSError("no such device"),
        ):
            result = ImageGenerateTool().execute(prompt="a cat")

        assert result.success is False
        assert "Failed to run local image generation" in result.content

    def test_nonzero_exit_surfaces_log_tail(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path)

        def _fake_run(args, **kwargs):
            # Nonzero exit and no output file written.
            return subprocess.CompletedProcess(
                args,
                1,
                stdout="loading model\nallocating\nerror: out of memory",
                stderr="",
            )

        with patch("openjarvis.tools.image_tool.subprocess.run", side_effect=_fake_run):
            result = ImageGenerateTool().execute(prompt="a cat")

        assert result.success is False
        assert "exit 1" in result.content
        assert "out of memory" in result.content

    def test_zero_exit_but_no_output_file_is_a_failure(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path)

        def _fake_run(args, **kwargs):
            # Exit 0 but the governor didn't actually produce a file.
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("openjarvis.tools.image_tool.subprocess.run", side_effect=_fake_run):
            result = ImageGenerateTool().execute(prompt="a cat")

        assert result.success is False

    def test_alt_text_strips_markdown_brackets(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path)

        def _fake_run(args, **kwargs):
            Path(args[-1]).write_bytes(b"fake")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("openjarvis.tools.image_tool.subprocess.run", side_effect=_fake_run):
            result = ImageGenerateTool().execute(prompt="a [scary] cat")

        assert result.success is True
        assert result.content.startswith("![a scary cat]")

    def test_output_path_traversal_is_contained_to_media_dir(
        self, monkeypatch, tmp_path
    ):
        """A model-supplied output_path must never let the tool write
        outside _MEDIA_IMAGES_DIR -- only the basename is honored."""
        self._patch_governor(monkeypatch, tmp_path)
        escape_target = tmp_path / "escaped.png"

        def _fake_run(args, **kwargs):
            Path(args[-1]).write_bytes(b"fake")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("openjarvis.tools.image_tool.subprocess.run", side_effect=_fake_run):
            result = ImageGenerateTool().execute(
                prompt="a cat", output_path="../../../../escaped.png"
            )

        assert result.success is True
        assert not escape_target.exists()
        assert Path(result.metadata["path"]).parent == tmp_path / "images"
        assert result.metadata["url"] == "/jarvis-media/images/escaped.png"

    def test_output_path_absolute_path_is_contained(self, monkeypatch, tmp_path):
        """Even a fully-writable absolute path elsewhere on disk must be
        reduced to just its basename inside the media dir -- output_path
        is a filename, never a real filesystem destination."""
        self._patch_governor(monkeypatch, tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        absolute_target = elsewhere / "evil.png"

        def _fake_run(args, **kwargs):
            Path(args[-1]).write_bytes(b"fake")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("openjarvis.tools.image_tool.subprocess.run", side_effect=_fake_run):
            result = ImageGenerateTool().execute(
                prompt="a cat", output_path=str(absolute_target)
            )

        assert result.success is True
        assert not absolute_target.exists()
        assert Path(result.metadata["path"]) == tmp_path / "images" / "evil.png"

    def test_default_provider_is_local(self, monkeypatch, tmp_path):
        """No `provider` param at all must take the local path, not openai."""
        self._patch_governor(monkeypatch, tmp_path)

        def _fake_run(args, **kwargs):
            Path(args[-1]).write_bytes(b"fake")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch(
            "openjarvis.tools.image_tool.subprocess.run", side_effect=_fake_run
        ) as mock_run:
            result = ImageGenerateTool().execute(prompt="a cat")

        assert result.success is True
        assert result.metadata["provider"] == "local"
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# OpenAI provider — opt-in via provider="openai"
# ---------------------------------------------------------------------------


class TestImageGenerateToolOpenAI:
    def test_invalid_size(self):
        tool = ImageGenerateTool()
        result = tool.execute(prompt="a cat", size="999x999", provider="openai")
        assert result.success is False
        assert "Invalid size" in result.content

    def test_openai_not_installed(self, monkeypatch):
        """Simulate openai package not being installed."""
        monkeypatch.delitem(sys.modules, "openai", raising=False)
        original_import = builtins.__import__

        def _mock_import(name, *args, **kwargs):
            if name == "openai":
                raise ImportError("No module named 'openai'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _mock_import)

        tool = ImageGenerateTool()
        result = tool.execute(prompt="a cat", provider="openai")
        assert result.success is False
        assert "openai package not installed" in result.content

    def test_no_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        mock_openai = MagicMock()
        monkeypatch.setitem(sys.modules, "openai", mock_openai)

        tool = ImageGenerateTool()
        result = tool.execute(prompt="a cat", provider="openai")
        assert result.success is False
        assert "No API key" in result.content

    def test_successful_generation(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        mock_image_data = MagicMock()
        mock_image_data.url = "https://example.com/image.png"

        mock_response = MagicMock()
        mock_response.data = [mock_image_data]

        mock_client = MagicMock()
        mock_client.images.generate.return_value = mock_response

        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        monkeypatch.setitem(sys.modules, "openai", mock_openai)

        tool = ImageGenerateTool()
        result = tool.execute(prompt="a cat on a mat", provider="openai")
        assert result.success is True
        assert result.content == "https://example.com/image.png"
        assert result.metadata["url"] == "https://example.com/image.png"
        assert result.metadata["size"] == "1024x1024"
        assert result.metadata["provider"] == "openai"

    def test_save_to_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setattr(
            "openjarvis.tools.image_tool._MEDIA_IMAGES_DIR", tmp_path / "images"
        )
        mock_image_data = MagicMock()
        mock_image_data.url = "https://example.com/image.png"

        mock_response = MagicMock()
        mock_response.data = [mock_image_data]

        mock_client = MagicMock()
        mock_client.images.generate.return_value = mock_response

        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        monkeypatch.setitem(sys.modules, "openai", mock_openai)

        # Mock httpx for downloading
        import httpx

        mock_http_resp = MagicMock()
        mock_http_resp.content = b"\x89PNG\r\n\x1a\nfake-image-data"
        mock_http_resp.raise_for_status = MagicMock()
        monkeypatch.setattr(httpx, "get", MagicMock(return_value=mock_http_resp))

        tool = ImageGenerateTool()
        result = tool.execute(
            prompt="a cat",
            provider="openai",
            output_path="output.png",
        )
        assert result.success is True
        saved = tmp_path / "images" / "output.png"
        assert saved.exists()
        assert saved.read_bytes() == b"\x89PNG\r\n\x1a\nfake-image-data"

    def test_save_to_file_contains_traversal_to_media_dir(self, monkeypatch, tmp_path):
        """The openai provider's save-to-file path shares the same
        containment as the local provider -- output_path is a filename,
        never an arbitrary filesystem destination."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setattr(
            "openjarvis.tools.image_tool._MEDIA_IMAGES_DIR", tmp_path / "images"
        )
        mock_image_data = MagicMock()
        mock_image_data.url = "https://example.com/image.png"
        mock_response = MagicMock()
        mock_response.data = [mock_image_data]
        mock_client = MagicMock()
        mock_client.images.generate.return_value = mock_response
        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        monkeypatch.setitem(sys.modules, "openai", mock_openai)

        import httpx

        mock_http_resp = MagicMock()
        mock_http_resp.content = b"payload"
        mock_http_resp.raise_for_status = MagicMock()
        monkeypatch.setattr(httpx, "get", MagicMock(return_value=mock_http_resp))

        escape_target = tmp_path / "escaped.png"
        tool = ImageGenerateTool()
        result = tool.execute(
            prompt="a cat",
            provider="openai",
            output_path="../escaped.png",
        )
        assert result.success is True
        assert not escape_target.exists()
        assert (tmp_path / "images" / "escaped.png").exists()

    def test_api_error(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_client.images.generate.side_effect = RuntimeError("Rate limit exceeded")

        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        monkeypatch.setitem(sys.modules, "openai", mock_openai)

        tool = ImageGenerateTool()
        result = tool.execute(prompt="a cat", provider="openai")
        assert result.success is False
        assert "Image generation error" in result.content
