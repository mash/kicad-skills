"""Netlist + fp-lib-table helpers used by ``pcb sync`` /
``pcb validate``.

Reuses the small S-expression parser style already in ``sch_netlist.py``, but
extends it to extract:

- per-component metadata (ref, value, footprint lib_id) from the
  ``(components ...)`` block
- per-net membership (ref + pin) from the ``(nets ...)`` block

Also provides ``resolve_footprint_path`` which walks the project-local
``fp-lib-table`` (and falls back to KiCad's global table /
``${KICAD10_FOOTPRINT_DIR}``) to map a ``LIB:Name`` lib_id to the
``.kicad_mod`` file on disk.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from sch_netlist import _tokenize, _parse, _is_list_with_head, _find_child, _child_value, _atom_value

KICAD10_FOOTPRINT_DIR_DEFAULT = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"
KICAD10_GLOBAL_FP_LIB_TABLE = Path.home() / "Library/Preferences/kicad/10.0/fp-lib-table"

# Whitelist of `attr` exclude flags that the PCB footprint header `(attr ...)`
# token list mirrors. Anything outside this set is preserved verbatim by Step 5.
_ATTR_EXCLUDE_FLAGS = frozenset({
    "exclude_from_bom",
    "exclude_from_pos_files",
    "exclude_from_board",
    "exclude_from_sim",
    "dnp",
})


def _parse_units_block(units_node: list[Any]) -> list[dict[str, Any]]:
    """Convert netlist ``(units (unit (name "A") (pins (pin (num "1")) ...)))``
    into the flat internal representation ``[{name, pins: [str, ...]}, ...]``."""
    out: list[dict[str, Any]] = []
    for child in units_node[1:]:
        if not _is_list_with_head(child, "unit"):
            continue
        name = _child_value(child, "name") or ""
        pins_node = _find_child(child, "pins")
        pin_nums: list[str] = []
        if pins_node is not None:
            for pn in pins_node[1:]:
                if not _is_list_with_head(pn, "pin"):
                    continue
                num = _child_value(pn, "num")
                if num:
                    pin_nums.append(num)
        out.append({"name": name, "pins": pin_nums})
    return out


def parse_components(netlist_path: str | Path) -> dict[str, dict[str, Any]]:
    """Return ``{ref: {value, footprint, libsource}}`` from a kicad netlist."""
    text = Path(netlist_path).read_text(encoding="utf-8")
    tree, _ = _parse(_tokenize(text), 0)
    if not isinstance(tree, list):
        raise ValueError("netlist root is not a list")
    comps_block = _find_child(tree, "components")
    out: dict[str, dict[str, Any]] = {}
    if comps_block is None:
        return out
    for comp in comps_block[1:]:
        if not _is_list_with_head(comp, "comp"):
            continue
        ref = _child_value(comp, "ref")
        if not ref:
            continue
        sheetpath = _find_child(comp, "sheetpath")
        sheet_names = ""
        sheet_tstamps = ""
        if sheetpath is not None:
            sheet_names = _child_value(sheetpath, "names") or ""
            sheet_tstamps = _child_value(sheetpath, "tstamps") or ""
        # Sheetfile is exposed as a (property (name "Sheetfile") (value "...")).
        # Also harvest property-Description and the value-less exclude flags in
        # a single pass over the comp's children.
        sheetfile = ""
        prop_description = ""
        attr_excludes: set[str] = set()
        for child in comp[1:]:
            if not _is_list_with_head(child, "property"):
                continue
            pname = _child_value(child, "name") or ""
            if pname == "Sheetfile" and not sheetfile:
                sheetfile = _child_value(child, "value") or ""
            elif pname == "Description" and not prop_description:
                prop_description = _child_value(child, "value") or ""
            elif pname in _ATTR_EXCLUDE_FLAGS:
                # Whitelist-only: the property has no (value ...) child;
                # presence alone implies the flag is set.
                if _find_child(child, "value") is None:
                    attr_excludes.add(pname)
        # Component instance UUID lives at the comp's top level as
        # (tstamps "<uuid>"). Note: there is also a (tstamps ...) inside
        # sheetpath; we want the one OUTSIDE sheetpath.
        comp_tstamps = ""
        for child in comp[1:]:
            if _is_list_with_head(child, "tstamps") and child is not sheetpath:
                comp_tstamps = _atom_value(child[1]) if len(child) > 1 else ""
                break
        # Description: top-level (description "...") wins over property
        # Description; either may be empty (Step 4 treats empty as skip).
        top_description = _child_value(comp, "description") or ""
        description = top_description or prop_description or ""

        # Units: netlist may carry a (units ...) sibling. Parse to flat form.
        units_node = _find_child(comp, "units")
        units = _parse_units_block(units_node) if units_node is not None else []

        if ref in out:
            # Multi-unit symbol case (U1A/U1B emitted as separate comps).
            # cupwarmer-hw does not exercise this — see plan Step 7 (TBD).
            existing = out[ref]
            # Merge units: union by name, keep ordered insertion.
            seen_names = {u["name"] for u in existing["units"]}
            for u in units:
                if u["name"] not in seen_names:
                    existing["units"].append(u)
                    seen_names.add(u["name"])
            existing["attr_excludes"] |= attr_excludes
            if not existing["description"] and description:
                existing["description"] = description
            continue
        out[ref] = {
            "ref": ref,
            "value": _child_value(comp, "value") or "",
            "footprint": _child_value(comp, "footprint") or "",
            "datasheet": _child_value(comp, "datasheet") or "",
            "description": description,
            "sheet_names": sheet_names,
            "sheet_tstamps": sheet_tstamps,
            "sheetfile": sheetfile,
            "tstamps": comp_tstamps,
            "units": units,
            "attr_excludes": attr_excludes,
        }
    return out


def parse_net_membership(netlist_path: str | Path) -> dict[str, list[tuple[str, str]]]:
    """Return ``{net_name: [(ref, pin), ...]}``."""
    text = Path(netlist_path).read_text(encoding="utf-8")
    tree, _ = _parse(_tokenize(text), 0)
    nets = _find_child(tree, "nets")
    out: dict[str, list[tuple[str, str]]] = {}
    if nets is None:
        return out
    for net in nets[1:]:
        if not _is_list_with_head(net, "net"):
            continue
        name = _child_value(net, "name") or ""
        members: list[tuple[str, str]] = []
        for n in net[1:]:
            if not _is_list_with_head(n, "node"):
                continue
            ref = _child_value(n, "ref") or ""
            pin = _child_value(n, "pin") or ""
            if ref and pin:
                members.append((ref, pin))
        members.sort()
        out[name] = members
    return out


def parse_node_pin_meta(
    netlist_path: str | Path,
) -> dict[tuple[str, str], dict[str, str]]:
    """Return ``{(ref, pin): {pinfunction, pintype}}`` from the ``(nets ...)``
    block.

    Mirrors :func:`parse_net_membership`'s walk but harvests the per-node
    ``(pinfunction "...")`` / ``(pintype "...")`` siblings. Entries are only
    emitted when at least one of the two values is present.
    """
    text = Path(netlist_path).read_text(encoding="utf-8")
    tree, _ = _parse(_tokenize(text), 0)
    nets = _find_child(tree, "nets")
    out: dict[tuple[str, str], dict[str, str]] = {}
    if nets is None:
        return out
    for net in nets[1:]:
        if not _is_list_with_head(net, "net"):
            continue
        for n in net[1:]:
            if not _is_list_with_head(n, "node"):
                continue
            ref = _child_value(n, "ref") or ""
            pin = _child_value(n, "pin") or ""
            if not (ref and pin):
                continue
            pinfunction = _child_value(n, "pinfunction")
            pintype = _child_value(n, "pintype")
            if pinfunction is None and pintype is None:
                continue
            entry: dict[str, str] = {}
            if pinfunction is not None:
                entry["pinfunction"] = pinfunction
            if pintype is not None:
                entry["pintype"] = pintype
            out[(ref, pin)] = entry
    return out


# ---------------------------------------------------------------------------
# fp-lib-table resolution
# ---------------------------------------------------------------------------


_LIB_LINE_RE = re.compile(
    r'\(lib\s+\(name\s+"([^"]+)"\)\s*\(type\s+"([^"]+)"\)\s*\(uri\s+"([^"]+)"\)'
)


def _read_fp_lib_table(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for m in _LIB_LINE_RE.finditer(text):
        name, _type, uri = m.group(1), m.group(2), m.group(3)
        out[name] = uri
    return out


def _expand_uri(uri: str, project_root: Path) -> str:
    abs_root = project_root.resolve() if isinstance(project_root, Path) else Path(os.path.abspath(project_root))
    s = uri.replace("${KIPRJMOD}", str(abs_root))
    fp_dir = os.environ.get("KICAD10_FOOTPRINT_DIR", KICAD10_FOOTPRINT_DIR_DEFAULT)
    s = s.replace("${KICAD10_FOOTPRINT_DIR}", fp_dir)
    s = s.replace("${KICAD9_FOOTPRINT_DIR}", fp_dir)
    s = s.replace("${KICAD8_FOOTPRINT_DIR}", fp_dir)
    s = os.path.expandvars(s)
    s = os.path.expanduser(s)
    return s


def load_fp_lib_table(project_dir: Path) -> dict[str, Path]:
    """Merged lib name -> .pretty directory mapping.

    Project table overrides the global table on collision.
    """
    merged: dict[str, str] = {}
    # Always seed from stock template so system libraries (Capacitor_SMD,
    # Resistor_SMD, ...) resolve even when the user's global fp-lib-table
    # only contains a `(type "Table")` indirection back to the stock table.
    stock_template = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/template/fp-lib-table")
    if stock_template.exists():
        merged.update(_read_fp_lib_table(stock_template))
    g = _read_fp_lib_table(KICAD10_GLOBAL_FP_LIB_TABLE)
    merged.update(g)
    proj = _read_fp_lib_table(project_dir / "fp-lib-table")
    merged.update(proj)
    return {name: Path(_expand_uri(uri, project_dir)) for name, uri in merged.items()}


def resolve_footprint_path(lib_id: str, project_dir: Path) -> Path | None:
    """Resolve ``LIB:Name`` to the ``.kicad_mod`` file. Returns None if not found."""
    if ":" not in lib_id:
        return None
    lib, name = lib_id.split(":", 1)
    table = load_fp_lib_table(project_dir)
    if lib not in table:
        return None
    candidate = table[lib] / f"{name}.kicad_mod"
    return candidate if candidate.exists() else None


__all__ = [
    "parse_components",
    "parse_net_membership",
    "parse_node_pin_meta",
    "load_fp_lib_table",
    "resolve_footprint_path",
]
