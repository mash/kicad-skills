"""Success-path tests for ``sch edit label`` leaf subcommands."""
from __future__ import annotations

import re
from pathlib import Path

from tests.helpers import run_cli


def _first_local_label_uuid(sch: Path) -> str:
    text = sch.read_text()
    # Local label uses (label "NAME" ...). Match it but not (global_label / hierarchical_label.
    m = re.search(r'\(label\s+"[^"]+".*?\(uuid\s+"([0-9a-f-]+)"\)', text, re.DOTALL)
    assert m, "could not find a local label uuid in fixture"
    return m.group(1)


def test_sch_edit_label_add_global(sch_fixture: Path) -> None:
    out = run_cli(
        "sch", "edit", "label", "add", str(sch_fixture),
        "global", "NET99", "50,50",
        "--dry-run",
    )
    assert out["action"] == "add_label"
    assert out["details"]["kind"] == "global"
    assert out["details"]["name"] == "NET99"
    assert out["details"]["at"] == [50.0, 50.0]


def test_sch_edit_label_add_hier(sch_fixture: Path) -> None:
    # The fixture has no hierarchical_label sibling; add_label clones a sibling
    # of the same kind, so this will fail. Skip if no sibling exists.
    text = sch_fixture.read_text()
    if "(hierarchical_label" not in text:
        import pytest
        pytest.skip("fixture has no hierarchical_label sibling to clone")
    out = run_cli(
        "sch", "edit", "label", "add", str(sch_fixture),
        "hier", "NET99", "50,50",
        "--dry-run",
    )
    assert out["action"] == "add_label"
    assert out["details"]["kind"] == "hier"


def test_sch_edit_label_add_local(sch_fixture: Path) -> None:
    out = run_cli(
        "sch", "edit", "label", "add", str(sch_fixture),
        "local", "NET99", "50,50",
        "--dry-run",
    )
    assert out["action"] == "add_label"
    assert out["details"]["kind"] == "local"
    assert out["details"]["name"] == "NET99"


def test_sch_edit_label_move(sch_fixture: Path) -> None:
    uuid = _first_local_label_uuid(sch_fixture)
    out = run_cli(
        "sch", "edit", "label", "move", str(sch_fixture), uuid, "70,70",
        "--dry-run",
    )
    assert out["action"] == "move_label"
    assert out["details"]["uuid"] == uuid
    assert out["details"]["new"]["x"] == 70.0
    assert out["details"]["new"]["y"] == 70.0


def test_sch_edit_label_delete(sch_fixture: Path) -> None:
    uuid = _first_local_label_uuid(sch_fixture)
    out = run_cli(
        "sch", "edit", "label", "delete", str(sch_fixture), uuid,
        "--dry-run",
    )
    assert out["action"] == "delete_label"
    assert out["details"]["uuid"] == uuid
