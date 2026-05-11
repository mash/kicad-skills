"""Success-path tests for ``kicad-tool pcb edit footprint *`` leaves.

All tests use ``--dry-run`` so the fixture file is not mutated.
"""
from __future__ import annotations

from tests.helpers import run_cli


def test_pcb_edit_footprint_move(pcb_fixture):
    out = run_cli(
        "pcb", "edit", "footprint", "move",
        str(pcb_fixture), "R1", "50,50",
        "--dry-run",
    )
    assert out["action"] == "move_footprint"
    assert out["details"]["ref"] == "R1"
    assert out["details"]["new"]["x"] == 50
    assert out["details"]["new"]["y"] == 50


def test_pcb_edit_footprint_move_with_rotation(pcb_fixture):
    out = run_cli(
        "pcb", "edit", "footprint", "move",
        str(pcb_fixture), "R1", "50,50",
        "--rotation", "90",
        "--dry-run",
    )
    assert out["action"] == "move_footprint"
    assert out["details"]["new"]["rotation"] == 90


def test_pcb_edit_footprint_move_property(pcb_fixture):
    out = run_cli(
        "pcb", "edit", "footprint", "move-property",
        str(pcb_fixture), "R1", "Reference", "55,55",
        "--dry-run",
    )
    assert out["details"]["ref"] == "R1"


def test_pcb_edit_footprint_set_property(pcb_fixture):
    out = run_cli(
        "pcb", "edit", "footprint", "set-property",
        str(pcb_fixture), "R1", "Value", "22k",
        "--dry-run",
    )
    assert out["details"]["ref"] == "R1"
    assert out["details"]["key"] == "Value"
    assert out["details"]["new"] == "22k"


def test_pcb_edit_footprint_move_layer_front(pcb_fixture):
    # R1 starts on F.Cu in the fixture; flip it to B.Cu first (real write
    # against the tmp_path copy), then exercise the ``front`` branch with
    # --dry-run as required.
    run_cli(
        "pcb", "edit", "footprint", "move-layer",
        str(pcb_fixture), "R1", "back",
    )
    out = run_cli(
        "pcb", "edit", "footprint", "move-layer",
        str(pcb_fixture), "R1", "front",
        "--dry-run",
    )
    assert out["details"]["ref"] == "R1"
    assert out["details"]["side"] == "front"


def test_pcb_edit_footprint_move_layer_back(pcb_fixture):
    out = run_cli(
        "pcb", "edit", "footprint", "move-layer",
        str(pcb_fixture), "R1", "back",
        "--dry-run",
    )
    assert out["details"]["ref"] == "R1"


def test_pcb_edit_footprint_delete(pcb_fixture):
    out = run_cli(
        "pcb", "edit", "footprint", "delete",
        str(pcb_fixture), "R1",
        "--dry-run",
    )
    assert out["details"]["ref"] == "R1"
