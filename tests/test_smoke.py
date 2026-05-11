"""Smoke test: verify the fixtures parse and the CLI reaches a leaf subcommand."""
from __future__ import annotations

from pathlib import Path

from tests.helpers import run_cli


def test_sch_query_list_symbols(sch_fixture: Path) -> None:
    out = run_cli("sch", "query", "list", str(sch_fixture), "symbols")
    refs = {item["ref"] for item in out["items"]}
    assert "R1" in refs
    assert "LED1" in refs
