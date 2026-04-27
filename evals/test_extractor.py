"""Eval harness for the extractor.

Hits the real Anthropic API. Costs money (~$0.05 per full run on Sonnet).
Skipped by default. Run explicitly:

    pytest --run-evals

Each fixture is `<name>.md` paired with `<name>.expected.json`:
    {"count_min": int, "count_max": int, "must_mention": [str, ...]}

Assertions per fixture:
- Result is a list[Ticket].
- count_min <= len <= count_max.
- For each must_mention keyword (case-insensitive), at least one ticket
  title OR description contains it.

The 04_chitchat_noop fixture is the regression catch for hallucination.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from src.extractor import Extractor

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _fixtures() -> list[tuple[Path, Path]]:
    pairs = []
    for md in sorted(FIXTURES_DIR.glob("*.md")):
        expected = md.with_suffix(".expected.json")
        if expected.exists():
            pairs.append((md, expected))
    return pairs


@pytest.fixture(scope="session")
def extractor() -> Extractor:
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    model = os.environ.get("EXTRACTOR_MODEL", "claude-sonnet-4-6")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    return Extractor(api_key=api_key, model=model)


@pytest.mark.eval
@pytest.mark.parametrize("notes_path,expected_path", _fixtures(), ids=lambda p: p.name)
def test_fixture(notes_path: Path, expected_path: Path, extractor: Extractor, request) -> None:
    if not request.config.getoption("--run-evals"):
        pytest.skip("eval tests are opt-in (use --run-evals)")

    notes = notes_path.read_text(encoding="utf-8")
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    tickets = extractor.extract(notes)

    count = len(tickets)
    cmin = expected["count_min"]
    cmax = expected["count_max"]
    assert cmin <= count <= cmax, (
        f"{notes_path.name}: expected {cmin}-{cmax} tickets, got {count}. "
        f"Titles: {[t.title for t in tickets]}"
    )

    for keyword in expected.get("must_mention", []):
        kw = keyword.lower()
        hits = [t for t in tickets if kw in t.title.lower() or kw in t.description.lower()]
        assert hits, (
            f"{notes_path.name}: expected at least one ticket mentioning {keyword!r}, "
            f"got titles: {[t.title for t in tickets]}"
        )
