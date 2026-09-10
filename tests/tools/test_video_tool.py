"""Tests for the video_generate tool.

All tests mock subprocess.run so the suite never shells out to the real
(up to an hour long) Wan2.2 governor script.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from openjarvis.tools.video_tool import VideoGenerateTool


class TestVideoGenerateToolSpec:
    def test_spec(self):
        tool = VideoGenerateTool()
        assert tool.spec.name == "video_generate"
        assert tool.spec.category == "media"
        assert "prompt" in tool.spec.parameters["properties"]
        assert "prompt" in tool.spec.parameters["required"]

    def test_tool_id(self):
        assert VideoGenerateTool().tool_id == "video_generate"

    def test_is_local(self):
        assert VideoGenerateTool().is_local is True

    def test_no_prompt(self):
        result = VideoGenerateTool().execute(prompt="")
        assert result.success is False
        assert "No prompt" in result.content

    def test_no_prompt_param(self):
        result = VideoGenerateTool().execute()
        assert result.success is False
        assert "No prompt" in result.content


class TestVideoGenerateToolExecute:
    def _patch_governor(self, monkeypatch, tmp_path, exists: bool = True):
        governor = tmp_path / "governor.sh"
        if exists:
            governor.write_text("#!/bin/sh\n")
            governor.chmod(0o755)
        monkeypatch.setattr(
            "openjarvis.tools.video_tool._LOCAL_GOVERNOR", str(governor)
        )
        monkeypatch.setattr(
            "openjarvis.tools.video_tool._MEDIA_VIDEOS_DIR", tmp_path / "videos"
        )
        return governor

    def test_governor_missing(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path, exists=False)
        result = VideoGenerateTool().execute(prompt="a dog running")
        assert result.success is False
        assert "not set up" in result.content

    def test_successful_generation(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path)

        def _fake_run(args, **kwargs):
            Path(args[-1]).write_bytes(b"fake-mp4")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("openjarvis.tools.video_tool.subprocess.run", side_effect=_fake_run):
            result = VideoGenerateTool().execute(prompt="a dog running")

        assert result.success is True
        assert result.content.startswith("[Video ansehen](/jarvis-media/videos/")
        assert "16:9" in result.content
        assert result.metadata["aspect_ratio"] == "16:9"
        assert result.metadata["url"].startswith("/jarvis-media/videos/")
        assert Path(result.metadata["path"]).exists()

    def test_default_aspect_ratio_is_16_9(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path)
        captured: dict[str, str] = {}

        def _fake_run(args, **kwargs):
            captured["aspect"] = args[2]
            Path(args[-1]).write_bytes(b"fake")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("openjarvis.tools.video_tool.subprocess.run", side_effect=_fake_run):
            VideoGenerateTool().execute(prompt="a dog running")

        assert captured["aspect"] == "16:9"

    def test_custom_aspect_ratio_passed_through(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path)
        captured: dict[str, str] = {}

        def _fake_run(args, **kwargs):
            captured["aspect"] = args[2]
            Path(args[-1]).write_bytes(b"fake")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("openjarvis.tools.video_tool.subprocess.run", side_effect=_fake_run):
            result = VideoGenerateTool().execute(
                prompt="a dog running", aspect_ratio="9:16"
            )

        assert result.success is True
        assert captured["aspect"] == "9:16"
        assert result.metadata["aspect_ratio"] == "9:16"

    def test_output_path_traversal_is_contained_to_media_dir(
        self, monkeypatch, tmp_path
    ):
        """A model-supplied output_path must never let the tool write
        outside _MEDIA_VIDEOS_DIR -- only the basename is honored."""
        self._patch_governor(monkeypatch, tmp_path)
        escape_target = tmp_path / "escaped.mp4"

        def _fake_run(args, **kwargs):
            Path(args[-1]).write_bytes(b"fake")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("openjarvis.tools.video_tool.subprocess.run", side_effect=_fake_run):
            result = VideoGenerateTool().execute(
                prompt="a dog running", output_path="../../../../escaped.mp4"
            )

        assert result.success is True
        assert not escape_target.exists()
        assert Path(result.metadata["path"]).parent == tmp_path / "videos"
        assert result.metadata["url"] == "/jarvis-media/videos/escaped.mp4"

    def test_invalid_aspect_ratio_falls_back_to_default(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path)
        captured: dict[str, str] = {}

        def _fake_run(args, **kwargs):
            captured["aspect"] = args[2]
            Path(args[-1]).write_bytes(b"fake")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("openjarvis.tools.video_tool.subprocess.run", side_effect=_fake_run):
            VideoGenerateTool().execute(
                prompt="a dog running", aspect_ratio="cinematic"
            )

        assert captured["aspect"] == "16:9"

    def test_timeout(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path)

        with patch(
            "openjarvis.tools.video_tool.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="governor", timeout=4500),
        ):
            result = VideoGenerateTool().execute(prompt="a dog running")

        assert result.success is False
        assert "timed out" in result.content

    def test_subprocess_raises(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path)

        with patch(
            "openjarvis.tools.video_tool.subprocess.run",
            side_effect=OSError("no such device"),
        ):
            result = VideoGenerateTool().execute(prompt="a dog running")

        assert result.success is False
        assert "Failed to run local video generation" in result.content

    def test_nonzero_exit_surfaces_log_tail(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path)

        def _fake_run(args, **kwargs):
            return subprocess.CompletedProcess(
                args, 1, stdout="loading\nerror: cuda oom", stderr=""
            )

        with patch("openjarvis.tools.video_tool.subprocess.run", side_effect=_fake_run):
            result = VideoGenerateTool().execute(prompt="a dog running")

        assert result.success is False
        assert "exit 1" in result.content
        assert "cuda oom" in result.content

    def test_zero_exit_but_no_output_file_is_a_failure(self, monkeypatch, tmp_path):
        self._patch_governor(monkeypatch, tmp_path)

        def _fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("openjarvis.tools.video_tool.subprocess.run", side_effect=_fake_run):
            result = VideoGenerateTool().execute(prompt="a dog running")

        assert result.success is False
