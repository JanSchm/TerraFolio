"""What the service needs to know that is not calibration.

Kept rigorously apart from the assumption set. That file holds every rate,
weight, floor, clamp, tolerance and band the *engine* uses, and changing one is
an auditable event that moves ``assumption_set_id`` and so invalidates the
comparability of every run (§9.4, §10.2). Nothing here touches a number the
engine reads: these are the paths the process runs against and the intervals at
which it polls a table. Putting a poll interval in the calibration would make
tuning the server look like changing the model.

Durations are named and carried in **milliseconds**, matching ``docs/api.md``'s
``durationMs`` throughout, so the wire, the store and this module all state an
elapsed time in one unit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from terrafolio.runner.modes import RunnerMode

__all__ = ["RunnerMode", "Settings", "repository_root"]
"""``RunnerMode`` is re-exported for callers that read it off the settings."""

MILLISECONDS_PER_SECOND: Final = 1000


def repository_root() -> Path:
    """The checkout this package was imported from, or the working directory.

    ``pipeline/``, ``assumptions/`` and ``web/`` live at the repository root
    because they are user-editable and belong in git (epic §4). An installed
    wheel has no such root, so the fallback is the working directory and the
    operator sets the paths explicitly.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd()


class Settings(BaseSettings):
    """Deployment configuration, overridable from the environment.

    Every field has a working default, so ``terrafolio serve`` in a checkout
    needs no arguments; ``TERRAFOLIO_`` prefixes each name in the environment.
    """

    model_config = SettingsConfigDict(env_prefix="TERRAFOLIO_", extra="forbid")

    pipeline_dir: Path = Field(default_factory=lambda: repository_root() / "pipeline")
    database_path: Path = Field(default_factory=lambda: repository_root() / "terrafolio.db")
    web_dir: Path = Field(default_factory=lambda: repository_root() / "web")
    assumption_set: str | None = None
    """The assumption set to load by name. ``None`` takes the shipped default."""

    runner_mode: RunnerMode = RunnerMode.PROCESS
    max_workers: int | None = None
    """``None`` resolves to ``max(1, cpu_count() - 1)``, leaving a core for the
    server itself. Oversubscribing is worse than it looks here: BLAS is pinned to
    one thread per worker precisely so reduction order cannot vary (§12)."""

    warm_workers: bool = True
    """Load the pipeline into each worker at startup, so the first real run does
    not pay for it. Off in tests that assert a cold worker's behaviour."""

    sse_poll_ms: int = 30
    """How often the stream re-reads ``run_event``. §10.3 wants a generation at
    least every 100 ms, and a generation takes about 40 ms at Standard, so this
    has to be well under both to avoid becoming the binding delay."""

    sse_keepalive_ms: int = 15000
    """Comment-frame interval, for proxies that close an idle connection during
    an Exhaustive run (``docs/api.md`` §7)."""

    @property
    def stylesheet_path(self) -> Path:
        """Tailwind's build output, which the committee pack inlines.

        A build artefact — ``web/.gitignore`` ignores ``dist/`` — so it is absent
        in a clean checkout until ``npm --prefix web run build`` has run. The pack
        says so by name rather than serving an unstyled document.
        """
        return self.web_dir / "dist" / "app.css"

    @property
    def fonts_dir(self) -> Path:
        return self.web_dir / "fonts"

    @property
    def atlas_path(self) -> Path:
        """Vendored Natural Earth 110m countries (``ui-contract.md`` §5.3)."""
        return self.web_dir / "public" / "countries-110m.json"
