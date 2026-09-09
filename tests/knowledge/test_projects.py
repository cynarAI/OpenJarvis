"""Tests for ProjectRegistry — cross-project knowledge indexing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from openjarvis.knowledge.projects import ProjectConfig, ProjectRegistry
from openjarvis.tools.storage._stubs import MemoryBackend, RetrievalResult


class _FakeMemory(MemoryBackend):
    """Fake backend with replace_source, matching SQLiteMemory's contract."""

    backend_id = "fake"

    def __init__(self) -> None:
        self._docs: dict[str, dict] = {}
        self._counter = 0
        self.replace_source_calls: list[tuple[str, int]] = []

    def store(self, content, *, source="", metadata=None):
        self._counter += 1
        doc_id = f"doc-{self._counter}"
        self._docs[doc_id] = {
            "content": content,
            "source": source,
            "metadata": metadata or {},
        }
        return doc_id

    def replace_source(self, source, documents):
        # Drop any existing docs for this source, then store the new set —
        # mirrors SQLiteMemory.replace_source's atomic-replace semantics.
        stale = [did for did, d in self._docs.items() if d["source"] == source]
        for did in stale:
            del self._docs[did]
        doc_ids = [
            self.store(content, source=source, metadata=metadata)
            for content, metadata in documents
        ]
        self.replace_source_calls.append((source, len(documents)))
        return doc_ids

    def retrieve(self, query, *, top_k=5, **kwargs):
        results = [
            RetrievalResult(
                content=d["content"],
                score=1.0,
                source=d["source"],
                metadata=d["metadata"],
            )
            for d in self._docs.values()
            if query.lower() in d["content"].lower() or query == d["source"]
        ]
        return results[:top_k]

    def delete(self, doc_id):
        return self._docs.pop(doc_id, None) is not None

    def clear(self):
        self._docs.clear()


class _NoReplaceMemory(MemoryBackend):
    """Fake backend WITHOUT replace_source — should be rejected."""

    backend_id = "no-replace"

    def store(self, content, *, source="", metadata=None):
        return "x"

    def retrieve(self, query, *, top_k=5, **kwargs):
        return []

    def delete(self, doc_id):
        return False

    def clear(self):
        pass


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Demo\nThis project does demo things.\n")
    return repo


@pytest.fixture()
def registry(project_dir: Path) -> ProjectRegistry:
    return ProjectRegistry(
        _FakeMemory(),
        projects=[ProjectConfig("demo", project_dir, github_repo="")],
    )


class TestProjectRegistryConstruction:
    def test_rejects_backend_without_replace_source(self, project_dir: Path) -> None:
        with pytest.raises(TypeError, match="replace_source"):
            ProjectRegistry(
                _NoReplaceMemory(), projects=[ProjectConfig("demo", project_dir)]
            )

    def test_project_ids(self, registry: ProjectRegistry) -> None:
        assert registry.project_ids == ["demo"]


class TestRefresh:
    def test_refresh_indexes_readme(self, registry: ProjectRegistry) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.returncode = 0
            count = registry.refresh("demo")
        assert count >= 1

    def test_refresh_unknown_project_raises(self, registry: ProjectRegistry) -> None:
        with pytest.raises(KeyError):
            registry.refresh("nope")

    def test_refresh_missing_repo_path_returns_zero(self, tmp_path: Path) -> None:
        registry = ProjectRegistry(
            _FakeMemory(),
            projects=[ProjectConfig("ghost", tmp_path / "does-not-exist")],
        )
        assert registry.refresh("ghost") == 0

    def test_refresh_is_idempotent_via_replace_source(
        self, registry: ProjectRegistry
    ) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.returncode = 0
            registry.refresh("demo")
            registry.refresh("demo")
        mem = registry._memory
        # Two refreshes of the same unchanged README must not accumulate docs.
        docs_for_demo = [d for d in mem._docs.values() if d["source"] == "demo"]
        assert len(docs_for_demo) == mem.replace_source_calls[-1][1]

    def test_refresh_all_continues_after_one_failure(self, project_dir: Path) -> None:
        mem = _FakeMemory()
        registry = ProjectRegistry(
            mem,
            projects=[
                ProjectConfig("broken", Path("/definitely/not/a/repo")),
                ProjectConfig("demo", project_dir),
            ],
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.returncode = 0
            results = registry.refresh_all()
        assert results["broken"] == 0
        assert results["demo"] >= 1

    def test_refresh_includes_issues_and_prs_when_configured(
        self, project_dir: Path
    ) -> None:
        registry = ProjectRegistry(
            _FakeMemory(),
            projects=[ProjectConfig("demo", project_dir, github_repo="acme/demo")],
        )

        def fake_run(cmd, **kwargs):
            result = type("R", (), {})()
            if cmd[0] == "gh":
                result.returncode = 0
                result.stdout = '[{"number": 1, "title": "Bug", "state": "OPEN"}]'
            else:
                result.returncode = 0
                result.stdout = ""
            return result

        with patch("subprocess.run", side_effect=fake_run):
            registry.refresh("demo")

        mem = registry._memory
        contents = [d["content"] for d in mem._docs.values() if d["source"] == "demo"]
        assert any("issues:" in c for c in contents)


class TestSummary:
    def test_summary_filters_by_project(self, project_dir: Path) -> None:
        mem = _FakeMemory()
        mem.store("demo content", source="demo")
        mem.store("other content", source="other-project")
        registry = ProjectRegistry(mem, projects=[ProjectConfig("demo", project_dir)])

        results = registry.summary("demo", "content")
        assert all(r.source == "demo" for r in results)
        assert any("demo content" in r.content for r in results)

    def test_summary_unknown_project_raises(self, registry: ProjectRegistry) -> None:
        with pytest.raises(KeyError):
            registry.summary("nope")
