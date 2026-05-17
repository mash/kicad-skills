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


# --- zone edit tests -------------------------------------------------------
import pytest

UUID_GND_F = "00000000-0000-0000-0000-00000000201e"
UUID_GND_B = "00000000-0000-0000-0000-00000000202e"
UUID_VCC_F = "00000000-0000-0000-0000-00000000203e"


def test_pcb_edit_zone_set_polygon_by_uuid(pcb_zones_fixture):
    out = run_cli(
        "pcb", "edit", "zone", "set-polygon",
        str(pcb_zones_fixture),
        "--uuid", UUID_GND_F,
        "10,10", "20,10", "20,20", "10,20",
        "--dry-run",
    )
    assert out["action"] == "set_zone_polygon"
    assert out["changed"] is True
    assert "diff" in out and out["diff"]
    assert "20" in out["diff"] and "10" in out["diff"]
    assert out["details"]["uuid"] == UUID_GND_F
    assert "points" in out["details"]
    assert out["details"]["area_mm2"] > 0


def test_pcb_edit_zone_set_polygon_by_name_single(pcb_zones_fixture):
    out = run_cli(
        "pcb", "edit", "zone", "set-polygon",
        str(pcb_zones_fixture),
        "--name", "VCC_POUR",
        "10,10", "20,10", "20,20", "10,20",
        "--dry-run",
    )
    assert out["action"] == "set_zone_polygon"
    assert out["changed"] is True


def test_pcb_edit_zone_set_polygon_multi_match_refuses(pcb_zones_fixture):
    with pytest.raises(RuntimeError):
        run_cli(
            "pcb", "edit", "zone", "set-polygon",
            str(pcb_zones_fixture),
            "--name", "GND_TOP",
            "10,10", "20,10", "20,20", "10,20",
            "--dry-run",
        )


def test_pcb_edit_zone_set_polygon_too_few_points(pcb_zones_fixture):
    with pytest.raises(RuntimeError):
        run_cli(
            "pcb", "edit", "zone", "set-polygon",
            str(pcb_zones_fixture),
            "--uuid", UUID_GND_F,
            "10,10", "20,10",
            "--dry-run",
        )


def test_pcb_edit_zone_set_polygon_consecutive_duplicate(pcb_zones_fixture):
    with pytest.raises(RuntimeError):
        run_cli(
            "pcb", "edit", "zone", "set-polygon",
            str(pcb_zones_fixture),
            "--uuid", UUID_GND_F,
            "10,10", "10,10", "20,20", "30,30",
            "--dry-run",
        )


def test_pcb_edit_zone_set_polygon_strips_filled_polygon(pcb_zones_fixture):
    out = run_cli(
        "pcb", "edit", "zone", "set-polygon",
        str(pcb_zones_fixture),
        "--uuid", UUID_GND_F,
        "10,10", "20,10", "20,20", "10,20",
        "--dry-run",
    )
    # Diff should show the filled_polygon being removed (as deletion lines),
    # but the new/post version of the zone must not contain a filled_polygon.
    # Each diff line starts with '+' (added) or '-' (removed). Verify no
    # added line introduces a filled_polygon for the target zone.
    added_lines = [
        ln for ln in out["diff"].splitlines()
        if ln.startswith("+") and not ln.startswith("+++")
    ]
    assert not any("filled_polygon" in ln for ln in added_lines)


def test_pcb_edit_zone_add_copy_settings(pcb_zones_fixture):
    out = run_cli(
        "pcb", "edit", "zone", "add",
        str(pcb_zones_fixture),
        "GND", "F.Cu",
        "80,40", "90,40", "90,50", "80,50",
        "--copy-settings-from-uuid", UUID_VCC_F,
        "--dry-run",
    )
    assert out["action"] == "add_zone"
    assert out["changed"] is True
    assert "(zone" in out["diff"]
    assert out["details"]["source_uuid"] == UUID_VCC_F


def test_pcb_edit_zone_add_unknown_net(pcb_zones_fixture):
    with pytest.raises(RuntimeError):
        run_cli(
            "pcb", "edit", "zone", "add",
            str(pcb_zones_fixture),
            "UNKNOWN_NET", "F.Cu",
            "80,40", "90,40", "90,50",
            "--copy-settings-from-uuid", UUID_VCC_F,
            "--dry-run",
        )


def test_pcb_edit_zone_add_unknown_layer(pcb_zones_fixture):
    with pytest.raises(RuntimeError):
        run_cli(
            "pcb", "edit", "zone", "add",
            str(pcb_zones_fixture),
            "GND", "UNKNOWN_LAYER",
            "80,40", "90,40", "90,50",
            "--copy-settings-from-uuid", UUID_VCC_F,
            "--dry-run",
        )


def test_pcb_edit_zone_add_missing_settings(pcb_zones_fixture):
    with pytest.raises(RuntimeError):
        run_cli(
            "pcb", "edit", "zone", "add",
            str(pcb_zones_fixture),
            "GND", "F.Cu",
            "80,40", "90,40", "90,50",
            "--dry-run",
        )


def test_pcb_edit_zone_delete_by_uuid(pcb_zones_fixture):
    out = run_cli(
        "pcb", "edit", "zone", "delete",
        str(pcb_zones_fixture),
        "--uuid", UUID_GND_F,
        "--dry-run",
    )
    assert out["action"] == "delete_zone"
    assert out["changed"] is True
    assert "diff" in out and out["diff"]
    assert out["details"]["uuid"] == UUID_GND_F
    assert "name" in out["details"]
    assert "layer" in out["details"]


def test_pcb_edit_zone_delete_multi_match_refuses(pcb_zones_fixture):
    with pytest.raises(RuntimeError):
        run_cli(
            "pcb", "edit", "zone", "delete",
            str(pcb_zones_fixture),
            "--name", "GND_TOP",
            "--dry-run",
        )


def test_pcb_edit_zone_set_property_priority(pcb_zones_fixture):
    out = run_cli(
        "pcb", "edit", "zone", "set-property",
        str(pcb_zones_fixture),
        "--uuid", UUID_GND_F,
        "priority", "99",
        "--dry-run",
    )
    assert out["action"] == "set_zone_property"
    assert out["changed"] is True
    assert out["details"]["old"] == 1
    assert out["details"]["new"] == 99


def test_pcb_edit_zone_set_property_priority_noop(pcb_zones_fixture):
    out = run_cli(
        "pcb", "edit", "zone", "set-property",
        str(pcb_zones_fixture),
        "--uuid", UUID_GND_F,
        "priority", "1",
        "--dry-run",
    )
    assert out["changed"] is False
    assert not out.get("diff")


def test_pcb_edit_zone_set_property_name(pcb_zones_fixture):
    out = run_cli(
        "pcb", "edit", "zone", "set-property",
        str(pcb_zones_fixture),
        "--uuid", UUID_GND_F,
        "name", "NEW_NAME",
        "--dry-run",
    )
    assert out["changed"] is True
    assert out["details"]["new"] == "NEW_NAME"


def test_pcb_edit_zone_set_property_clearance(pcb_zones_fixture):
    out = run_cli(
        "pcb", "edit", "zone", "set-property",
        str(pcb_zones_fixture),
        "--uuid", UUID_GND_F,
        "clearance", "0.6",
        "--dry-run",
    )
    assert out["changed"] is True
    assert float(out["details"]["new"]) == 0.6


def test_pcb_edit_zone_set_property_unknown_key(pcb_zones_fixture):
    # argparse choices= violation surfaces as non-zero exit → RuntimeError
    with pytest.raises((RuntimeError, SystemExit)):
        run_cli(
            "pcb", "edit", "zone", "set-property",
            str(pcb_zones_fixture),
            "--uuid", UUID_GND_F,
            "nonexistent_key", "1",
            "--dry-run",
        )


def test_pcb_edit_zone_set_property_strips_filled_polygon(pcb_zones_fixture):
    out = run_cli(
        "pcb", "edit", "zone", "set-property",
        str(pcb_zones_fixture),
        "--uuid", UUID_GND_F,
        "priority", "99",
        "--dry-run",
    )
    added_lines = [
        ln for ln in out["diff"].splitlines()
        if ln.startswith("+") and not ln.startswith("+++")
    ]
    assert not any("filled_polygon" in ln for ln in added_lines)
