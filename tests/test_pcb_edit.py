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


def test_pcb_edit_zone_set_polygon_collinear_zero_area(pcb_zones_fixture):
    """Three distinct but collinear points yield shoelace area 0 → reject."""
    with pytest.raises(RuntimeError):
        run_cli(
            "pcb", "edit", "zone", "set-polygon",
            str(pcb_zones_fixture),
            "--uuid", UUID_GND_F,
            "0,0", "10,0", "20,0",
            "--dry-run",
        )


def test_pcb_edit_zone_set_polygon_strips_filled_polygon(pcb_zones_fixture):
    # Originally each of the 3 zones in the fixture has one filled_polygon
    # block (3 total). After set-polygon on one zone, that zone's
    # filled_polygon must be stripped, leaving 2 occurrences in the file.
    before = pcb_zones_fixture.read_text(encoding="utf-8").count("(filled_polygon")
    assert before == 3
    run_cli(
        "pcb", "edit", "zone", "set-polygon",
        str(pcb_zones_fixture),
        "--uuid", UUID_GND_F,
        "10,10", "20,10", "20,20", "10,20",
    )
    after = pcb_zones_fixture.read_text(encoding="utf-8").count("(filled_polygon")
    assert after == 2


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


# --- via edit tests --------------------------------------------------------
UUID_VIA_GND = "00000000-0000-0000-0000-0000000030a1"
UUID_VIA_VCC = "00000000-0000-0000-0000-0000000030a2"


def test_pcb_edit_via_add(pcb_vias_fixture):
    out = run_cli(
        "pcb", "edit", "via", "add",
        str(pcb_vias_fixture),
        "GND", "50,50",
    )
    assert out["action"] == "add_via"
    assert out["changed"] is True
    assert out["details"]["net"] == "GND"
    assert out["details"]["at"]["x"] == 50.0
    # Re-load and verify the via count grew.
    listing = run_cli("pcb", "query", "list", str(pcb_vias_fixture), "vias")
    assert len(listing["items"]) == 3


def test_pcb_edit_via_add_unknown_net(pcb_vias_fixture):
    with pytest.raises(RuntimeError):
        run_cli(
            "pcb", "edit", "via", "add",
            str(pcb_vias_fixture),
            "NOPE", "50,50",
            "--dry-run",
        )


def test_pcb_edit_via_add_idempotent_uuid(pcb_vias_fixture):
    a = run_cli(
        "pcb", "edit", "via", "add",
        str(pcb_vias_fixture),
        "GND", "70,70",
        "--dry-run",
    )
    b = run_cli(
        "pcb", "edit", "via", "add",
        str(pcb_vias_fixture),
        "GND", "70,70",
        "--dry-run",
    )
    assert a["details"]["uuid"] == b["details"]["uuid"]


def test_pcb_edit_via_delete_by_uuid(pcb_vias_fixture):
    out = run_cli(
        "pcb", "edit", "via", "delete",
        str(pcb_vias_fixture),
        "--uuid", UUID_VIA_GND,
    )
    assert out["action"] == "delete_via"
    assert out["changed"] is True
    listing = run_cli("pcb", "query", "list", str(pcb_vias_fixture), "vias")
    uuids = {it["uuid"] for it in listing["items"]}
    assert UUID_VIA_GND not in uuids
    assert UUID_VIA_VCC in uuids


def test_pcb_edit_via_delete_by_at(pcb_vias_fixture):
    out = run_cli(
        "pcb", "edit", "via", "delete",
        str(pcb_vias_fixture),
        "--at", "100,100",
        "--dry-run",
    )
    assert out["action"] == "delete_via"
    assert out["details"]["uuid"] == UUID_VIA_GND


def test_pcb_edit_via_delete_ambiguous(pcb_vias_fixture):
    with pytest.raises(RuntimeError):
        run_cli(
            "pcb", "edit", "via", "delete",
            str(pcb_vias_fixture),
            "--at", "105,100", "--tolerance", "10",
            "--dry-run",
        )


def test_pcb_edit_via_move_by_uuid(pcb_vias_fixture):
    out = run_cli(
        "pcb", "edit", "via", "move",
        str(pcb_vias_fixture),
        "--uuid", UUID_VIA_GND,
        "55,66",
    )
    assert out["action"] == "move_via"
    assert out["details"]["new"]["x"] == 55
    assert out["details"]["new"]["y"] == 66
    # Other fields unchanged.
    q = run_cli(
        "pcb", "query", "via", str(pcb_vias_fixture),
        "--uuid", UUID_VIA_GND,
    )
    assert q["at"]["x"] == 55
    assert q["at"]["y"] == 66
    assert q["size"] == 0.8
    assert q["drill"] == 0.4
    assert q["net"] == "GND"
    assert q["layers"] == ["F.Cu", "B.Cu"]


def test_pcb_edit_via_set_property_size(pcb_vias_fixture):
    out = run_cli(
        "pcb", "edit", "via", "set-property",
        str(pcb_vias_fixture),
        "--uuid", UUID_VIA_GND,
        "size", "1.0",
        "--dry-run",
    )
    assert out["changed"] is True
    assert out["details"]["new"] == 1.0


def test_pcb_edit_via_set_property_drill(pcb_vias_fixture):
    out = run_cli(
        "pcb", "edit", "via", "set-property",
        str(pcb_vias_fixture),
        "--uuid", UUID_VIA_GND,
        "drill", "0.5",
        "--dry-run",
    )
    assert out["changed"] is True
    assert out["details"]["new"] == 0.5


def test_pcb_edit_via_set_property_net(pcb_vias_fixture):
    out = run_cli(
        "pcb", "edit", "via", "set-property",
        str(pcb_vias_fixture),
        "--uuid", UUID_VIA_GND,
        "net", "VCC",
        "--dry-run",
    )
    assert out["changed"] is True
    assert out["details"]["new"] == "VCC"
    assert out["details"]["old"] == "GND"


def test_pcb_edit_via_set_property_net_unknown(pcb_vias_fixture):
    with pytest.raises(RuntimeError):
        run_cli(
            "pcb", "edit", "via", "set-property",
            str(pcb_vias_fixture),
            "--uuid", UUID_VIA_GND,
            "net", "NOPE",
            "--dry-run",
        )


def test_pcb_edit_via_set_property_layers(pcb_vias_fixture):
    out = run_cli(
        "pcb", "edit", "via", "set-property",
        str(pcb_vias_fixture),
        "--uuid", UUID_VIA_GND,
        "layers", "B.Cu,F.Cu",
        "--dry-run",
    )
    assert out["changed"] is True
    assert out["details"]["new"] == ["B.Cu", "F.Cu"]


def test_pcb_edit_via_set_property_layers_unknown(pcb_vias_fixture):
    with pytest.raises(RuntimeError):
        run_cli(
            "pcb", "edit", "via", "set-property",
            str(pcb_vias_fixture),
            "--uuid", UUID_VIA_GND,
            "layers", "F.Cu,BOGUS",
            "--dry-run",
        )


def test_pcb_edit_via_set_property_free(pcb_vias_fixture):
    out = run_cli(
        "pcb", "edit", "via", "set-property",
        str(pcb_vias_fixture),
        "--uuid", UUID_VIA_GND,
        "free", "yes",
        "--dry-run",
    )
    assert out["changed"] is True
    assert out["details"]["new"] is True


def test_pcb_edit_via_set_property_locked(pcb_vias_fixture):
    out = run_cli(
        "pcb", "edit", "via", "set-property",
        str(pcb_vias_fixture),
        "--uuid", UUID_VIA_GND,
        "locked", "true",
        "--dry-run",
    )
    assert out["changed"] is True
    assert out["details"]["new"] is True


# --- via invariants / edge cases (followup) -------------------------------
import re as _re
import difflib as _difflib


def _top_block_counts(text: str) -> dict[str, int]:
    """Count top-level KiCad blocks by head token (depth-1)."""
    return {
        "footprint": len(_re.findall(r"^\t\(footprint\b", text, _re.MULTILINE)),
        "zone": len(_re.findall(r"^\t\(zone\b", text, _re.MULTILINE)),
        "segment": len(_re.findall(r"^\t\(segment\b", text, _re.MULTILINE)),
        "net": len(_re.findall(r"^\t\(net\s+\d+\b", text, _re.MULTILINE)),
        "via": len(_re.findall(r"^\t\(via\b", text, _re.MULTILINE)),
    }


def test_pcb_edit_via_add_preserves_other_block_counts(pcb_vias_fixture):
    before = pcb_vias_fixture.read_text(encoding="utf-8")
    bc = _top_block_counts(before)
    run_cli(
        "pcb", "edit", "via", "add",
        str(pcb_vias_fixture), "GND", "60,60",
    )
    after = pcb_vias_fixture.read_text(encoding="utf-8")
    ac = _top_block_counts(after)
    assert ac["via"] == bc["via"] + 1
    for k in ("footprint", "zone", "segment", "net"):
        assert ac[k] == bc[k]


def test_pcb_edit_via_delete_preserves_other_block_counts(pcb_vias_fixture):
    before = pcb_vias_fixture.read_text(encoding="utf-8")
    bc = _top_block_counts(before)
    run_cli(
        "pcb", "edit", "via", "delete",
        str(pcb_vias_fixture), "--uuid", UUID_VIA_GND,
    )
    after = pcb_vias_fixture.read_text(encoding="utf-8")
    ac = _top_block_counts(after)
    assert ac["via"] == bc["via"] - 1
    for k in ("footprint", "zone", "segment", "net"):
        assert ac[k] == bc[k]


def test_pcb_edit_via_move_preserves_other_block_counts(pcb_vias_fixture):
    before = pcb_vias_fixture.read_text(encoding="utf-8")
    bc = _top_block_counts(before)
    run_cli(
        "pcb", "edit", "via", "move",
        str(pcb_vias_fixture), "--uuid", UUID_VIA_GND, "77,88",
    )
    after = pcb_vias_fixture.read_text(encoding="utf-8")
    ac = _top_block_counts(after)
    assert ac == bc


def test_pcb_edit_via_set_property_preserves_other_block_counts(pcb_vias_fixture):
    before = pcb_vias_fixture.read_text(encoding="utf-8")
    bc = _top_block_counts(before)
    run_cli(
        "pcb", "edit", "via", "set-property",
        str(pcb_vias_fixture), "--uuid", UUID_VIA_GND, "size", "1.2",
    )
    after = pcb_vias_fixture.read_text(encoding="utf-8")
    ac = _top_block_counts(after)
    assert ac == bc


def test_pcb_edit_via_move_byte_diff_is_at_line_only(pcb_vias_fixture):
    before = pcb_vias_fixture.read_text(encoding="utf-8").splitlines()
    run_cli(
        "pcb", "edit", "via", "move",
        str(pcb_vias_fixture), "--uuid", UUID_VIA_GND, "77,88",
    )
    after = pcb_vias_fixture.read_text(encoding="utf-8").splitlines()
    diff = list(_difflib.unified_diff(before, after, lineterm=""))
    plus = [ln for ln in diff if ln.startswith("+") and not ln.startswith("+++")]
    minus = [ln for ln in diff if ln.startswith("-") and not ln.startswith("---")]
    assert len(plus) == 1
    assert len(minus) == 1
    assert "(at " in plus[0]
    assert "(at " in minus[0]


def test_pcb_edit_via_dry_run_add(pcb_vias_fixture):
    before = pcb_vias_fixture.read_text(encoding="utf-8")
    out = run_cli(
        "pcb", "edit", "via", "add",
        str(pcb_vias_fixture), "GND", "60,60", "--dry-run",
    )
    assert out["diff"]
    # On-disk content must be unchanged in dry-run.
    assert pcb_vias_fixture.read_text(encoding="utf-8") == before


def test_pcb_edit_via_dry_run_delete(pcb_vias_fixture):
    before = pcb_vias_fixture.read_text(encoding="utf-8")
    out = run_cli(
        "pcb", "edit", "via", "delete",
        str(pcb_vias_fixture), "--uuid", UUID_VIA_GND, "--dry-run",
    )
    assert out["diff"]
    assert pcb_vias_fixture.read_text(encoding="utf-8") == before


def test_pcb_edit_via_dry_run_move(pcb_vias_fixture):
    before = pcb_vias_fixture.read_text(encoding="utf-8")
    out = run_cli(
        "pcb", "edit", "via", "move",
        str(pcb_vias_fixture), "--uuid", UUID_VIA_GND, "77,88", "--dry-run",
    )
    assert out["diff"]
    assert pcb_vias_fixture.read_text(encoding="utf-8") == before


def test_pcb_edit_via_dry_run_set_property(pcb_vias_fixture):
    before = pcb_vias_fixture.read_text(encoding="utf-8")
    out = run_cli(
        "pcb", "edit", "via", "set-property",
        str(pcb_vias_fixture), "--uuid", UUID_VIA_GND, "size", "1.0", "--dry-run",
    )
    assert out["diff"]
    assert pcb_vias_fixture.read_text(encoding="utf-8") == before


def test_pcb_edit_via_set_property_free_toggle_off(pcb_vias_fixture):
    # The VCC via in the fixture has (free yes); toggling to no should
    # remove the (free ...) subform entirely.
    out = run_cli(
        "pcb", "edit", "via", "set-property",
        str(pcb_vias_fixture), "--uuid", UUID_VIA_VCC, "free", "no",
    )
    assert out["changed"] is True
    assert out["details"]["new"] is False
    text = pcb_vias_fixture.read_text(encoding="utf-8")
    # Locate the VCC via block and ensure (free ...) is absent.
    m = _re.search(
        r"\(via\b[^()]*(?:\([^()]*\)[^()]*)*\(uuid \"" + UUID_VIA_VCC + r"\"",
        text, _re.DOTALL,
    )
    assert m is not None
    assert not _re.search(r"\(free\s+\w+\s*\)", m.group(0))


def test_pcb_edit_via_cli_uuid_and_at_mutex(pcb_vias_fixture):
    with pytest.raises(RuntimeError):
        run_cli(
            "pcb", "edit", "via", "set-property",
            str(pcb_vias_fixture),
            "--uuid", UUID_VIA_GND, "--at", "100,100",
            "size", "1.0", "--dry-run",
        )


def test_pcb_edit_via_add_defaults_size_drill_from_setup(pcb_vias_fixture):
    out = run_cli(
        "pcb", "edit", "via", "add",
        str(pcb_vias_fixture), "GND", "60,60", "--dry-run",
    )
    assert out["details"]["size"] == 0.8
    assert out["details"]["drill"] == 0.4


def test_pcb_edit_via_add_layers_order_uuid_invariant(pcb_vias_fixture):
    # Sorted-layers UUID seed: adding the same (net, xy) twice with reversed
    # layer order must collide on UUID; second add is a no-op.
    a = run_cli(
        "pcb", "edit", "via", "add",
        str(pcb_vias_fixture), "GND", "60,60",
        "--layers", "B.Cu,F.Cu",
    )
    b = run_cli(
        "pcb", "edit", "via", "add",
        str(pcb_vias_fixture), "GND", "60,60",
        "--layers", "F.Cu,B.Cu",
        "--dry-run",
    )
    assert a["details"]["uuid"] == b["details"]["uuid"]


def test_pcb_edit_via_add_rejects_single_layer(pcb_vias_fixture):
    with pytest.raises(RuntimeError):
        run_cli(
            "pcb", "edit", "via", "add",
            str(pcb_vias_fixture), "GND", "60,60",
            "--layers", "F.Cu", "--dry-run",
        )


def test_pcb_edit_via_set_property_layers_rejects_single(pcb_vias_fixture):
    with pytest.raises(RuntimeError):
        run_cli(
            "pcb", "edit", "via", "set-property",
            str(pcb_vias_fixture), "--uuid", UUID_VIA_GND,
            "layers", "F.Cu", "--dry-run",
        )
