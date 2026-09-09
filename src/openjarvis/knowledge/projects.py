"""Indexes each EVO project's README, recent git history, and open GitHub
issues/PRs into a ``MemoryBackend`` so the brain can answer "what do you
know about project X" without re-reading each repo on every question.

Uses ``replace_source(project_id, documents)`` for idempotent re-indexing:
every refresh atomically replaces the previous snapshot for that project,
so a 15-30 minute polling timer (see Phase 2 plan) never accumulates
stale duplicates.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openjarvis.tools.storage._stubs import MemoryBackend, RetrievalResult
from openjarvis.tools.storage.ingest import ingest_path

logger = logging.getLogger(__name__)

_GIT_LOG_LIMIT = 50
_ISSUE_PR_LIMIT = 20
_GH_TIMEOUT = 15.0
_GIT_TIMEOUT = 10.0


@dataclass(frozen=True)
class ProjectConfig:
    """One project's identity: where it lives, where its docs are, and
    which GitHub repo (if any) to pull issues/PRs from."""

    project_id: str
    repo_path: Path
    github_repo: str = ""  # "owner/repo"; empty disables issue/PR indexing
    doc_paths: Tuple[str, ...] = field(default_factory=lambda: ("README.md",))


# EVO's homelab projects, per the "Jarvis auf EVO" plan's cross-project
# knowledge requirement. Paths and GitHub slugs verified against each
# repo's actual checkout + `git remote get-url origin` on 2026-09-09.
DEFAULT_PROJECTS: List[ProjectConfig] = [
    ProjectConfig("openjarvis", Path("/srv/openjarvis"), "cynarAI/OpenJarvis"),
    ProjectConfig("sugar-rush", Path("/srv/sugar-rush/repo"), "cynarAI/sugar-rush"),
    ProjectConfig("trading-bot", Path("/srv/trading-bot"), "cynarAI/tradingbot"),
    ProjectConfig("apex", Path("/srv/apex"), "cynarAI/Nexus"),
    ProjectConfig("homelab", Path("/srv/homelab"), "cynarAI/homelab"),
]


class ProjectRegistry:
    """Indexes and queries per-project knowledge in a ``MemoryBackend``."""

    def __init__(
        self,
        memory: MemoryBackend,
        projects: Optional[List[ProjectConfig]] = None,
    ) -> None:
        if not hasattr(memory, "replace_source"):
            raise TypeError(
                f"{type(memory).__name__} does not support replace_source(); "
                "ProjectRegistry needs a backend that can atomically replace "
                "all documents for one source (e.g. SQLiteMemory)."
            )
        self._memory = memory
        self._projects: Dict[str, ProjectConfig] = {
            p.project_id: p for p in (projects or DEFAULT_PROJECTS)
        }

    @property
    def project_ids(self) -> List[str]:
        return list(self._projects.keys())

    def refresh(self, project_id: str) -> int:
        """Re-index one project. Returns the number of documents stored."""
        project = self._projects.get(project_id)
        if project is None:
            raise KeyError(f"Unknown project {project_id!r}")
        if not project.repo_path.exists():
            logger.warning(
                "Project %r repo path %s does not exist; skipping",
                project_id,
                project.repo_path,
            )
            return 0

        documents: List[Tuple[str, Optional[Dict[str, Any]]]] = []
        documents.extend(self._doc_chunks(project))

        git_log = self._read_git_log(project.repo_path)
        if git_log:
            documents.append((git_log, {"source_type": "git_log"}))

        if project.github_repo:
            issues_prs = self._read_issues_and_prs(project.github_repo)
            if issues_prs:
                documents.append((issues_prs, {"source_type": "issues_prs"}))

        doc_ids = self._memory.replace_source(project_id, documents)
        logger.info("Refreshed project %r: %d documents", project_id, len(doc_ids))
        return len(doc_ids)

    def refresh_all(self) -> Dict[str, int]:
        """Refresh every registered project. One failure doesn't stop the rest."""
        results: Dict[str, int] = {}
        for project_id in self._projects:
            try:
                results[project_id] = self.refresh(project_id)
            except Exception:
                logger.warning(
                    "Failed to refresh project %r", project_id, exc_info=True
                )
                results[project_id] = 0
        return results

    def summary(
        self, project_id: str, query: str = "", *, top_k: int = 10
    ) -> List[RetrievalResult]:
        """Search *query* within one project's indexed knowledge.

        Over-fetches and filters by ``source`` client-side since
        ``MemoryBackend.retrieve()`` has no built-in per-source filter.
        """
        if project_id not in self._projects:
            raise KeyError(f"Unknown project {project_id!r}")
        search_query = query or project_id
        results = self._memory.retrieve(search_query, top_k=top_k * 3)
        return [r for r in results if r.source == project_id][:top_k]

    def _doc_chunks(
        self, project: ProjectConfig
    ) -> List[Tuple[str, Optional[Dict[str, Any]]]]:
        chunks: List[Tuple[str, Optional[Dict[str, Any]]]] = []
        for rel in project.doc_paths:
            doc_path = project.repo_path / rel
            if not doc_path.exists():
                continue
            try:
                for chunk in ingest_path(doc_path):
                    chunks.append((chunk.content, {"source_type": "doc", "path": rel}))
            except Exception:
                logger.warning("Failed to ingest %s", doc_path, exc_info=True)
        return chunks

    @staticmethod
    def _read_git_log(repo_path: Path) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_path), "log", "--oneline", f"-{_GIT_LOG_LIMIT}"],
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT,
                check=False,
            )
            return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("git log failed for %s: %s", repo_path, exc)
            return ""

    @staticmethod
    def _read_issues_and_prs(github_repo: str) -> str:
        parts = []
        for kind, args in (
            (
                "issues",
                [
                    "issue",
                    "list",
                    "--repo",
                    github_repo,
                    "--limit",
                    str(_ISSUE_PR_LIMIT),
                    "--json",
                    "number,title,state",
                ],
            ),
            (
                "prs",
                [
                    "pr",
                    "list",
                    "--repo",
                    github_repo,
                    "--limit",
                    str(_ISSUE_PR_LIMIT),
                    "--json",
                    "number,title,state",
                ],
            ),
        ):
            try:
                result = subprocess.run(
                    ["gh", *args],
                    capture_output=True,
                    text=True,
                    timeout=_GH_TIMEOUT,
                    check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    parts.append(f"{kind}:\n{result.stdout.strip()}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                logger.debug("gh %s failed for %s: %s", kind, github_repo, exc)
        return "\n\n".join(parts)


__all__ = ["DEFAULT_PROJECTS", "ProjectConfig", "ProjectRegistry"]
