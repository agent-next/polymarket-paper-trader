"""Load and validate market set YAML files."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

_BUNDLE_DIR = Path(__file__).parent.parent / "market_sets"

VALID_DIFFICULTIES = ("easy", "medium", "hard")


@dataclass(frozen=True)
class MarketEntry:
    """A single market in a benchmark set."""

    slug: str
    category: str = "general"
    difficulty: str = "medium"
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.slug:
            raise ValueError("MarketEntry slug cannot be empty")
        if self.difficulty not in VALID_DIFFICULTIES:
            raise ValueError(
                f"Invalid difficulty '{self.difficulty}', "
                f"must be one of {VALID_DIFFICULTIES}"
            )


@dataclass(frozen=True)
class MarketSet:
    """A collection of markets for benchmarking."""

    name: str
    description: str
    version: str
    markets: list[MarketEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("MarketSet name cannot be empty")
        if not self.markets:
            raise ValueError("MarketSet must contain at least one market")


def load_market_set(name_or_path: str) -> MarketSet:
    """Load a market set by bundled name or file path.

    Bundled sets are looked up in the market_sets/ directory.
    Full paths are loaded directly.
    """
    path = Path(name_or_path)
    if not path.suffix:
        path = _BUNDLE_DIR / f"{name_or_path}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Market set not found: {path}")

    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid market set format in {path}")

    markets = [
        MarketEntry(
            slug=m["slug"],
            category=m.get("category", "general"),
            difficulty=m.get("difficulty", "medium"),
            notes=m.get("notes", ""),
        )
        for m in raw.get("markets", [])
    ]

    return MarketSet(
        name=raw.get("name", path.stem),
        description=raw.get("description", ""),
        version=raw.get("version", "1.0"),
        markets=markets,
    )


def list_market_sets() -> list[str]:
    """Return names of all bundled market sets."""
    if not _BUNDLE_DIR.exists():
        return []
    return sorted(p.stem for p in _BUNDLE_DIR.glob("*.yaml"))
