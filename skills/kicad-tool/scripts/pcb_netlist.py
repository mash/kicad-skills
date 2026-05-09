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
        sheetfile = ""
        for child in comp[1:]:
            if (
                _is_list_with_head(child, "property")
                and _child_value(child, "name") == "Sheetfile"
            ):
                sheetfile = _child_value(child, "value") or ""
                break
        # Component instance UUID lives at the comp's top level as
        # (tstamps "<uuid>"). Note: there is also a (tstamps ...) inside
        # sheetpath; we want the one OUTSIDE sheetpath.
        comp_tstamps = ""
        for child in comp[1:]:
            if _is_list_with_head(child, "tstamps") and child is not sheetpath:
                comp_tstamps = _atom_value(child[1]) if len(child) > 1 else ""
                break
        out[ref] = {
            "ref": ref,
            "value": _child_value(comp, "value") or "",
            "footprint": _child_value(comp, "footprint") or "",
            "datasheet": _child_value(comp, "datasheet") or "",
            "sheet_names": sheet_names,
            "sheet_tstamps": sheet_tstamps,
            "sheetfile": sheetfile,
            "tstamps": comp_tstamps,
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
    "load_fp_lib_table",
    "resolve_footprint_path",
]
