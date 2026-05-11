"""Success-path tests for ``sch edit junction`` leaf subcommands."""
from __future__ import annotations

import re
from pathlib import Path

from tests.helpers import run_cli


def _first_junction_uuid(sch: Path) -> str:
    text = sch.read_text()
    m = re.search(r'\(junction\b.*?\(uuid\s+"([0-9a-f-]+)"\)', text, re.DOTALL)
    assert m, "could not find a junction uuid in fixture"
    return m.group(1)


def test_sch_edit_junction_add(sch_fixture: Path) -> None:
    out = run_cli(
        "sch", "edit", "junction", "add", str(sch_fixture), "80,80",
        "--dry-run",
    )
    assert out["action"] == "add_junction"
    assert out["details"]["at"] == [80.0, 80.0]
    assert "uuid" in out["details"]


def test_sch_edit_junction_delete(sch_fixture: Path) -> None:
    uuid = _first_junction_uuid(sch_fixture)
    out = run_cli(
        "sch", "edit", "junction", "delete", str(sch_fixture), uuid,
        "--dry-run",
    )
    assert out["action"] == "delete_junction"
    assert out["details"]["uuid"] == uuid
