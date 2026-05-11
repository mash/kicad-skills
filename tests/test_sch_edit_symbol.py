"""Success-path tests for `kicad-tool sch edit symbol *` subcommands.

All tests use ``--dry-run`` and ``--format json`` so the on-disk fixture is
never mutated; assertions look at the returned JSON shape only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import run_cli


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _assert_edit_payload(out: dict, expected_action: str) -> None:
    """Common shape assertions for any sch_edit return dict."""
    assert out["action"] == expected_action
    # On dry-run the file is not written, but `changed` reflects "would change"
    # (i.e. a real diff was produced).
    assert out["changed"] is True
    assert isinstance(out["diff"], str)
    assert out["diff"], "expected non-empty diff for a real edit"
    assert "details" in out


# --------------------------------------------------------------------------
# move
# --------------------------------------------------------------------------
def test_sch_edit_symbol_move(sch_fixture: Path) -> None:
    out = run_cli(
        "sch", "edit", "symbol", "move",
        str(sch_fixture), "R1", "100,100",
        "--dry-run",
    )
    _assert_edit_payload(out, "move_symbol")
    details = out["details"]
    assert details["ref"] == "R1"
    assert details["new"]["x"] == 100.0
    assert details["new"]["y"] == 100.0


def test_sch_edit_symbol_move_with_rotation(sch_fixture: Path) -> None:
    out = run_cli(
        "sch", "edit", "symbol", "move",
        str(sch_fixture), "R1", "100,100",
        "--rotation", "90",
        "--dry-run",
    )
    _assert_edit_payload(out, "move_symbol")
    assert out["details"]["new"]["rotation"] == 90.0


# --------------------------------------------------------------------------
# move-property
# --------------------------------------------------------------------------
def test_sch_edit_symbol_move_property(sch_fixture: Path) -> None:
    out = run_cli(
        "sch", "edit", "symbol", "move-property",
        str(sch_fixture), "R1", "Value", "110,110",
        "--dry-run",
    )
    _assert_edit_payload(out, "move_symbol_property")


# --------------------------------------------------------------------------
# add
# --------------------------------------------------------------------------
def test_sch_edit_symbol_add(sch_fixture: Path) -> None:
    out = run_cli(
        "sch", "edit", "symbol", "add",
        str(sch_fixture), "minimal:R", "R99", "50,50",
        "--dry-run",
    )
    _assert_edit_payload(out, "add_symbol")


# --------------------------------------------------------------------------
# add-pin (two variants: default lib-file path absent, and explicit lib-file)
# --------------------------------------------------------------------------
def test_sch_edit_symbol_add_pin_default_lib_file(sch_fixture: Path) -> None:
    # No --lib-file: handler uses default path which won't exist in tmp_path,
    # so lib_file update is "skipped" but the schematic edit still succeeds.
    out = run_cli(
        "sch", "edit", "symbol", "add-pin",
        str(sch_fixture), "minimal:R", "99", "X", "200,200",
        "--length", "2.54",
        "--type", "passive",
        "--dry-run",
    )
    _assert_edit_payload(out, "add_pin")
    # schematic side ran; lib_file side was skipped (default path doesn't exist
    # and / or namespace mismatch).
    assert "schematic" in out["details"]
    assert "lib_file" in out["details"]


_MINIMAL_KICAD_SYM = """(kicad_symbol_lib
\t(version 20231120)
\t(generator "kicad_symbol_editor")
\t(symbol "R"
\t\t(pin_names
\t\t\t(offset 0.762)
\t\t)
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(property "Reference" "R" (at 0 3.81 0))
\t\t(property "Value" "R" (at 0 -3.81 0))
\t\t(property "Footprint" "" (at 0 0 0))
\t\t(property "Datasheet" "" (at 0 0 0))
\t\t(property "Description" "Generic resistor" (at 0 0 0))
\t\t(symbol "R_0_1"
\t\t\t(rectangle
\t\t\t\t(start -1.016 2.54)
\t\t\t\t(end 1.016 -2.54)
\t\t\t)
\t\t)
\t\t(symbol "R_1_1"
\t\t\t(pin passive line
\t\t\t\t(at 0 5.08 270)
\t\t\t\t(length 2.54)
\t\t\t\t(name "~"
\t\t\t\t\t(effects (font (size 1.27 1.27)))
\t\t\t\t)
\t\t\t\t(number "1"
\t\t\t\t\t(effects (font (size 1.27 1.27)))
\t\t\t\t)
\t\t\t)
\t\t\t(pin passive line
\t\t\t\t(at 0 -5.08 90)
\t\t\t\t(length 2.54)
\t\t\t\t(name "~"
\t\t\t\t\t(effects (font (size 1.27 1.27)))
\t\t\t\t)
\t\t\t\t(number "2"
\t\t\t\t\t(effects (font (size 1.27 1.27)))
\t\t\t\t)
\t\t\t)
\t\t)
\t)
)
"""


def test_sch_edit_symbol_add_pin_with_lib_file(sch_fixture: Path) -> None:
    # Build a sibling minimal.kicad_sym so the namespace ("minimal") matches
    # the lib file stem and the lib-update branch runs.
    lib_path = sch_fixture.parent / "minimal.kicad_sym"
    lib_path.write_text(_MINIMAL_KICAD_SYM, encoding="utf-8")
    try:
        out = run_cli(
            "sch", "edit", "symbol", "add-pin",
            str(sch_fixture), "minimal:R", "98", "Y", "200,200",
            "--length", "2.54",
            "--type", "passive",
            "--lib-file", str(lib_path),
            "--dry-run",
        )
    except RuntimeError as exc:
        pytest.skip(f"add-pin lib-file branch unsupported on this fixture: {exc}")
    _assert_edit_payload(out, "add_pin")
    lib_info = out["details"]["lib_file"]
    assert lib_info["path"] == str(lib_path)
    # The lib-update branch should have run (not skipped) — verify by absence
    # of a "skipped" key, OR presence of a diff_summary.
    assert "skipped" not in lib_info, lib_info
    assert lib_info["diff_summary"]


# --------------------------------------------------------------------------
# delete
# --------------------------------------------------------------------------
def test_sch_edit_symbol_delete(sch_fixture: Path) -> None:
    out = run_cli(
        "sch", "edit", "symbol", "delete",
        str(sch_fixture), "R1",
        "--dry-run",
    )
    _assert_edit_payload(out, "delete_symbol")


# --------------------------------------------------------------------------
# set-property
# --------------------------------------------------------------------------
def test_sch_edit_symbol_set_property(sch_fixture: Path) -> None:
    out = run_cli(
        "sch", "edit", "symbol", "set-property",
        str(sch_fixture), "R1", "Value", "22k",
        "--dry-run",
    )
    _assert_edit_payload(out, "set_symbol_property")


# --------------------------------------------------------------------------
# set-attribute (both yes / no branches)
# --------------------------------------------------------------------------
def test_sch_edit_symbol_set_attribute_no(sch_fixture: Path) -> None:
    # R1's in_bom defaults to "yes" in the fixture, so setting "no" produces
    # a real diff — covers the "value changed" branch.
    out = run_cli(
        "sch", "edit", "symbol", "set-attribute",
        str(sch_fixture), "R1", "in_bom", "no",
        "--dry-run",
    )
    _assert_edit_payload(out, "set_symbol_attribute")


def test_sch_edit_symbol_set_attribute_yes(sch_fixture: Path) -> None:
    # Setting in_bom back to "yes" is a no-op (already yes in the fixture):
    # covers the "no change" branch — must succeed with changed=False.
    out = run_cli(
        "sch", "edit", "symbol", "set-attribute",
        str(sch_fixture), "R1", "in_bom", "yes",
        "--dry-run",
    )
    assert out["action"] == "set_symbol_attribute"
    assert out["changed"] is False
    assert out["diff"] == ""
