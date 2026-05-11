"""Success-path tests for ``sch edit wire`` leaf subcommands."""
from __future__ import annotations

import re
from pathlib import Path

from tests.helpers import run_cli


def _first_wire_uuid(sch: Path) -> str:
    text = sch.read_text()
    m = re.search(r'\(wire\b.*?\(uuid\s+"([0-9a-f-]+)"\)', text, re.DOTALL)
    assert m, "could not find a wire uuid in fixture"
    return m.group(1)


def test_sch_edit_wire_add_default(sch_fixture: Path) -> None:
    out = run_cli(
        "sch", "edit", "wire", "add", str(sch_fixture),
        "50,50", "60,60",
        "--dry-run",
    )
    assert out["action"] == "add_wire"
    assert out["details"]["from"] == [50.0, 50.0]
    assert out["details"]["to"] == [60.0, 60.0]
    assert out["details"]["type"] == "default"
    assert "uuid" in out["details"]


def test_sch_edit_wire_add_solid(sch_fixture: Path) -> None:
    out = run_cli(
        "sch", "edit", "wire", "add", str(sch_fixture),
        "50,50", "60,60",
        "--type", "solid",
        "--dry-run",
    )
    assert out["action"] == "add_wire"
    assert out["details"]["type"] == "solid"


def test_sch_edit_wire_delete(sch_fixture: Path) -> None:
    uuid = _first_wire_uuid(sch_fixture)
    out = run_cli(
        "sch", "edit", "wire", "delete", str(sch_fixture), uuid,
        "--dry-run",
    )
    assert out["action"] == "delete_wire"
    assert out["details"]["uuid"] == uuid
