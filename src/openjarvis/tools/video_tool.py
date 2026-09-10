"""Video generation tool -- local Wan2.2-T2V-A14B via stable-diffusion.cpp.

Much higher quality than NEXUS's Wan2.1-1.3B pipeline (14B MoE model,
high-noise + low-noise expert), at the cost of a longer generation time
(several minutes) and a temporary VRAM squeeze -- see
media-gen-governor-jarvis-video.sh for the resource-governor details.
"""

from __future__ import annotations

import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_ASPECT_RE = re.compile(r"^\d{1,2}:\d{1,2}$")
_LOCAL_GOVERNOR = "/srv/media-gen/bin/media-gen-governor-jarvis-video.sh"
_MEDIA_VIDEOS_DIR = Path("/srv/media-gen/videos")
_LOCAL_TIMEOUT_SECONDS = (
    4500  # Wan2.2-14B MoE two-pass sampling measured at ~54min; generous buffer
)


@ToolRegistry.register("video_generate")
class VideoGenerateTool(BaseTool):
    """Generate a short video clip from a text description."""

    tool_id = "video_generate"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="video_generate",
            description=(
                "Generate a short (~2 second) video clip from a text "
                "description using a local, full-quality video diffusion "
                "model (Wan2.2-14B). Takes up to an hour to run (two MoE "
                "sampling passes) -- say so if the user is waiting. Returns "
                "markdown of the form [Video ansehen](/jarvis-media/videos/...) "
                "-- you MUST copy that markdown into your reply to the user "
                "EXACTLY as returned (do not paraphrase, describe, or omit "
                "it) so the link actually works; add at most a short "
                "sentence before or after it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Text description of the video to generate.",
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "description": (
                            "Aspect ratio: '16:9' (default, landscape), "
                            "'9:16' (portrait), or '1:1' (square)."
                        ),
                    },
                    "output_path": {
                        "type": "string",
                        "description": (
                            "Optional file path (.mp4) to save the video to."
                        ),
                    },
                },
                "required": ["prompt"],
            },
            category="media",
            timeout_seconds=4600,
        )

    def execute(self, **params: Any) -> ToolResult:
        prompt = params.get("prompt", "")
        if not prompt:
            return ToolResult(
                tool_name="video_generate",
                content="No prompt provided.",
                success=False,
            )

        aspect = params.get("aspect_ratio") or "16:9"
        if not _ASPECT_RE.match(aspect):
            aspect = "16:9"

        if not Path(_LOCAL_GOVERNOR).exists():
            return ToolResult(
                tool_name="video_generate",
                content=(
                    f"Local video generation is not set up on this host "
                    f"({_LOCAL_GOVERNOR} missing)."
                ),
                success=False,
            )

        _MEDIA_VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = params.get("output_path")
        outfile = output_path or str(
            _MEDIA_VIDEOS_DIR
            / f"jarvis-gen-{int(time.time())}-{uuid.uuid4().hex[:8]}.mp4"
        )

        try:
            proc = subprocess.run(
                [_LOCAL_GOVERNOR, prompt, aspect, outfile],
                capture_output=True,
                text=True,
                timeout=_LOCAL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                tool_name="video_generate",
                content=(
                    f"Video generation timed out after {_LOCAL_TIMEOUT_SECONDS}s."
                ),
                success=False,
            )
        except Exception as exc:
            return ToolResult(
                tool_name="video_generate",
                content=f"Failed to run local video generation: {exc}",
                success=False,
            )

        if proc.returncode != 0 or not Path(outfile).is_file():
            tail = "\n".join((proc.stdout or "").splitlines()[-15:])
            return ToolResult(
                tool_name="video_generate",
                content=(
                    f"Local video generation failed (exit {proc.returncode}). "
                    f"Last log lines:\n{tail}"
                ),
                success=False,
            )

        filename = Path(outfile).name
        url = f"/jarvis-media/videos/{filename}"
        content = f"[Video ansehen]({url}) ({aspect}, lokal generiert mit Wan2.2-14B)"

        return ToolResult(
            tool_name="video_generate",
            content=content,
            success=True,
            metadata={"path": outfile, "url": url, "aspect_ratio": aspect},
        )


__all__ = ["VideoGenerateTool"]
