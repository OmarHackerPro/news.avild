"""Shared Rich progress bar for CLI scripts.

All backfill/admin scripts import make_script_progress() instead of
re-declaring the same 10-column Progress setup each time.

Usage:
    from rich.console import Console
    from app.utils.progress import make_script_progress

    console = Console()
    with make_script_progress(console) as progress:
        task = progress.add_task("description", total=total)
        progress.advance(task)
        progress.update(task, description="new description")
"""
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)


def make_script_progress(console: Console, refresh_per_second: int = 4) -> Progress:
    """Return a Progress instance with the standard kiber script columns."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        refresh_per_second=refresh_per_second,
    )
