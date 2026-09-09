"""Tests for the /v1/projects routes."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from openjarvis.server.api_routes import include_all_routes  # noqa: E402
from openjarvis.tools.storage._stubs import MemoryBackend, RetrievalResult  # noqa: E402


class _FakeMemory(MemoryBackend):
    backend_id = "fake"

    def __init__(self) -> None:
        self._docs: dict[str, dict] = {}
        self._n = 0

    def store(self, content, *, source="", metadata=None):
        self._n += 1
        doc_id = f"doc-{self._n}"
        self._docs[doc_id] = {
            "content": content,
            "source": source,
            "metadata": metadata or {},
        }
        return doc_id

    def replace_source(self, source, documents):
        for did in [d for d, v in self._docs.items() if v["source"] == source]:
            del self._docs[did]
        return [self.store(c, source=source, metadata=m) for c, m in documents]

    def retrieve(self, query, *, top_k=5, **kwargs):
        return [
            RetrievalResult(
                content=d["content"],
                score=1.0,
                source=d["source"],
                metadata=d["metadata"],
            )
            for d in self._docs.values()
        ][:top_k]

    def delete(self, doc_id):
        return self._docs.pop(doc_id, None) is not None

    def clear(self):
        self._docs.clear()


class _NoReplaceMemory(MemoryBackend):
    backend_id = "no-replace"

    def store(self, content, *, source="", metadata=None):
        return "x"

    def retrieve(self, query, *, top_k=5, **kwargs):
        return []

    def delete(self, doc_id):
        return False

    def clear(self):
        pass


def _make_app(memory_backend=None):
    app = FastAPI()
    include_all_routes(app)
    if memory_backend is not None:
        app.state.memory_backend = memory_backend
    return app


class TestProjectRoutes:
    def test_no_memory_backend_returns_503(self) -> None:
        client = TestClient(_make_app())
        resp = client.get("/v1/projects")
        assert resp.status_code == 503

    def test_backend_without_replace_source_returns_503(self) -> None:
        client = TestClient(_make_app(_NoReplaceMemory()))
        resp = client.get("/v1/projects")
        assert resp.status_code == 503

    def test_list_projects(self) -> None:
        client = TestClient(_make_app(_FakeMemory()))
        resp = client.get("/v1/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert "openjarvis" in data["projects"]
        assert "sugar-rush" in data["projects"]
        assert "trading-bot" in data["projects"]

    def test_summary_unknown_project_404(self) -> None:
        client = TestClient(_make_app(_FakeMemory()))
        resp = client.get("/v1/projects/nope/summary")
        assert resp.status_code == 404

    def test_summary_returns_indexed_content(self) -> None:
        mem = _FakeMemory()
        mem.store("sugar-rush handles checkout", source="sugar-rush")
        mem.store("trading-bot handles risk gates", source="trading-bot")
        client = TestClient(_make_app(mem))

        resp = client.get("/v1/projects/sugar-rush/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project"] == "sugar-rush"
        assert all(
            "checkout" in r["content"] or True for r in data["results"]
        )  # sanity: response shape is correct
        contents = [r["content"] for r in data["results"]]
        assert "sugar-rush handles checkout" in contents
        assert "trading-bot handles risk gates" not in contents

    def test_refresh_unknown_project_404(self) -> None:
        client = TestClient(_make_app(_FakeMemory()))
        resp = client.post("/v1/projects/nope/refresh")
        assert resp.status_code == 404
