"""``jarvis knowledge`` — cross-project knowledge indexing subcommands."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from openjarvis.cli.memory_cmd import _get_backend
from openjarvis.knowledge.projects import DEFAULT_PROJECTS, ProjectRegistry


@click.group()
def knowledge() -> None:
    """Manage cross-project knowledge indexing."""


@knowledge.command()
@click.argument("project_id", required=False)
@click.option(
    "--all", "refresh_all", is_flag=True, help="Refresh every registered project."
)
@click.option(
    "--backend", "-b", default=None, help="Override the default memory backend."
)
def refresh(project_id: str | None, refresh_all: bool, backend: str | None) -> None:
    """Re-index PROJECT_ID's (or --all projects') README, git log, issues/PRs."""
    console = Console()
    if not project_id and not refresh_all:
        raise click.ClickException("Specify a PROJECT_ID or pass --all.")

    mem = _get_backend(backend)
    try:
        registry = ProjectRegistry(mem)
        if refresh_all:
            results = registry.refresh_all()
        else:
            if project_id not in registry.project_ids:
                raise click.ClickException(
                    f"Unknown project {project_id!r}. "
                    f"Known: {', '.join(registry.project_ids)}"
                )
            results = {project_id: registry.refresh(project_id)}
    finally:
        if hasattr(mem, "close"):
            mem.close()

    table = Table(title="Knowledge Refresh")
    table.add_column("Project", style="cyan")
    table.add_column("Documents")
    for pid, count in results.items():
        table.add_row(pid, str(count))
    console.print(table)


@knowledge.command(name="list")
def list_projects() -> None:
    """List registered projects and where their knowledge comes from."""
    console = Console()
    table = Table(title="Registered Projects")
    table.add_column("Project", style="cyan")
    table.add_column("Repo Path")
    table.add_column("GitHub")
    for p in DEFAULT_PROJECTS:
        table.add_row(p.project_id, str(p.repo_path), p.github_repo or "-")
    console.print(table)


__all__ = ["knowledge"]
