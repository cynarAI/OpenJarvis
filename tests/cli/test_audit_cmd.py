"""Tests for ``jarvis audit`` — the behavior-compliance self-audit CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from openjarvis.cli import audit_cmd, cli


@dataclass
class _FakeTrace:
    query: str = ""
    result: str = ""


class _FakeTraceStore:
    def __init__(self, traces):
        self._traces = traces
        self.closed = False

    def list_traces(self, *, since=None, until=None, limit=100, **kwargs):
        return self._traces

    def close(self):
        self.closed = True


def _fake_response(json_body: dict, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx

        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


class TestDigestTraces:
    def test_builds_readable_digest(self):
        traces = [_FakeTrace(query="hi", result="hello")]
        digest = audit_cmd._digest_traces(traces)
        assert "hi" in digest and "hello" in digest

    def test_skips_empty_traces(self):
        traces = [_FakeTrace(query="", result="")]
        assert audit_cmd._digest_traces(traces) == ""

    def test_truncates_long_content(self):
        traces = [_FakeTrace(query="x" * 500, result="y" * 500)]
        digest = audit_cmd._digest_traces(traces)
        assert len(digest) < 1000


class TestExtractJson:
    def test_extracts_clean_json(self):
        result = audit_cmd._extract_json('{"drift_found": false, "summary": ""}')
        assert result == {"drift_found": False, "summary": ""}

    def test_extracts_json_from_surrounding_prose(self):
        text = 'Sure:\n{"drift_found": true, "summary": "test"}\nHope that helps.'
        result = audit_cmd._extract_json(text)
        assert result == {"drift_found": True, "summary": "test"}

    def test_returns_none_for_garbage(self):
        assert audit_cmd._extract_json("no json here at all") is None

    def test_returns_none_for_malformed_json(self):
        assert audit_cmd._extract_json("{not: valid json}") is None


class TestNotifyTelegram:
    def test_returns_false_without_credentials(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_OWNER_ID", raising=False)
        assert audit_cmd._notify_telegram("hi") is False

    def test_sends_message_with_credentials(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("TELEGRAM_OWNER_ID", "42")
        with patch("httpx.post", return_value=_fake_response({}, 200)) as mock_post:
            assert audit_cmd._notify_telegram("hi") is True
        assert mock_post.call_args.kwargs["json"]["chat_id"] == 42

    def test_returns_false_on_network_error(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("TELEGRAM_OWNER_ID", "42")
        import httpx

        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            assert audit_cmd._notify_telegram("hi") is False


class TestAuditRun:
    def test_no_activity_short_circuits(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            audit_cmd, "_constitution_path", lambda: _write_constitution(tmp_path)
        )
        with patch(
            "openjarvis.traces.store.TraceStore", lambda **kw: _FakeTraceStore([])
        ):
            with patch("openjarvis.core.config.load_config", MagicMock()):
                result = CliRunner().invoke(cli, ["audit", "run"])
        assert result.exit_code == 0
        assert "No activity" in result.output

    def test_no_drift_found(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            audit_cmd, "_constitution_path", lambda: _write_constitution(tmp_path)
        )
        traces = [_FakeTrace(query="fix bug", result="fixed, PR merged")]
        response = _fake_response({"content": '{"drift_found": false, "summary": ""}'})
        with patch(
            "openjarvis.traces.store.TraceStore", lambda **kw: _FakeTraceStore(traces)
        ):
            with patch("openjarvis.core.config.load_config", MagicMock()):
                with patch("httpx.post", return_value=response):
                    result = CliRunner().invoke(cli, ["audit", "run"])
        assert result.exit_code == 0
        assert "No drift found" in result.output

    def test_drift_found_notifies(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            audit_cmd, "_constitution_path", lambda: _write_constitution(tmp_path)
        )
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("TELEGRAM_OWNER_ID", "42")
        traces = [
            _FakeTrace(
                query="do the trivial thing", result="soll ich das wirklich tun?"
            )
        ]
        review_response = _fake_response(
            {"content": '{"drift_found": true, "summary": "unnötig nachgefragt"}'}
        )
        telegram_response = _fake_response({}, 200)

        def fake_post(url, **kwargs):
            return telegram_response if "telegram.org" in url else review_response

        with patch(
            "openjarvis.traces.store.TraceStore", lambda **kw: _FakeTraceStore(traces)
        ):
            with patch("openjarvis.core.config.load_config", MagicMock()):
                with patch("httpx.post", side_effect=fake_post):
                    result = CliRunner().invoke(cli, ["audit", "run"])
        assert result.exit_code == 0
        assert "Drift found" in result.output

    def test_missing_constitution_exits_nonzero(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            audit_cmd, "_constitution_path", lambda: tmp_path / "missing.md"
        )
        result = CliRunner().invoke(cli, ["audit", "run"])
        assert result.exit_code != 0


def _write_constitution(tmp_path: Path) -> Path:
    p = tmp_path / "constitution.md"
    p.write_text("# Test Constitution\nBe direct.\n")
    return p
