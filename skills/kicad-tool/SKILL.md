---
name: kicad-tool
description: Use the bundled unified KiCad CLI for schematic rendering, visual inspection/scoring, ERC, netlist export, validation, and PCB DRC. This skill owns `scripts/kicad_tool.py` and its helper modules.
---

# KiCad Tool

Single owner of KiCad automation commands. Other KiCad skills should call this CLI, never the helper modules directly.

## Entrypoint

Use the `kicad_tool.py` script bundled in this skill:

```bash
python3 <path-to-kicad-tool-skill>/scripts/kicad_tool.py <domain> <command> ...
```

When this plugin is checked out inside a repository, the path is usually:

```bash
python3 plugins/kicad-skills/skills/kicad-tool/scripts/kicad_tool.py <domain> <command> ...
```

`KICAD_CLI` overrides the executable. Default: `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`.

## Dependencies

Before using the scripts in a new environment, check whether the Python dependencies are available:

```bash
python3 -c "import kiutils"
```

If `kiutils` is missing, install this repository's Python dependencies from the repository root:

```bash
python3 -m pip install .
```

The `kiutils` dependency is a fork pinned in `pyproject.toml`. If the environment requires approval for network access or package installation, ask before installing. Do not silently vendor, rewrite, or bypass the dependency.

## Argument Conventions

- `<schematic>` is always the first positional for schematic commands.
- Required args are positional, in table order.
- Coordinates are a single `X,Y`; bounding boxes are `X1,Y1,X2,Y2`.
- Rotation is `--rotation R`.
- `--flag` is only for optional knobs, mutex selectors such as `--uuid` vs `--at`, and universal `--dry-run` / `--format {json,text}`.
- Do not invent flags for required args. `--bbox`, `--ref`, and `--symbol` are not command conventions.

## Commands

| Command | Purpose | Key flags |
|---|---|---|
| `sch render-region <sheet> <X1,Y1,X2,Y2>` | Render a cropped PNG of a schematic region. Use this whenever the model needs bounded visual input. | `--output <path>` default `tmp/region.png` |
| `sch inspect <sheet>` | Score + collisions. Re-renders SVG internally. | `--only-text <regex>`, `--margin` |
| `sch erc <top>` | Hierarchical ERC from the top-level schematic. | `--report <path>` |
| `sch netlist <top>` | Netlist export from the top-level schematic. | `--output <path>` |
| `sch validate <top>` | ERC + netlist + inspect in one call. | `--sheet <edited>`, `--save-baseline DIR`, `--baseline DIR` |
| `pcb drc <board>` | PCB DRC. | `--report <path>`, `--schematic-parity` |

## Query

Read-only schematic inspection. Query commands emit JSON by default.

| Command | Purpose |
|---|---|
| `sch query symbol <sch> <REF>` | Symbol details by reference. |
| `sch query pin <sch> <REF.PIN> [--netlist PATH]` | Pin coordinates, orientation, and optional net. |
| `sch query net <sch> <NAME_OR_REF.PIN> --netlist PATH` | Net members and labels. |
| `sch query region <sch> <X1,Y1,X2,Y2>` | Elements inside a bounding box. |
| `sch query wire <sch> (--uuid U \| --at X,Y \| --through X,Y)` | Locate a wire. |
| `sch query label <sch> (--name N \| --uuid U)` | Locate labels. |
| `sch query lib-symbol <sch> <LIB_ID>` | Library symbol definition. |
| `sch query list <sch> <KIND> [--netlist PATH]` | Enumerate `symbols`, `labels`, `wires`, `junctions`, or `nets`. |

## Edit

Structural schematic mutations. All support `--dry-run` and `--format {json,text}`.

| Command | Purpose |
|---|---|
| `sch edit symbol move <sch> <REF> <X,Y> [--rotation R]` | Move or rotate a symbol. |
| `sch edit symbol move-property <sch> <REF> <KEY> <X,Y> [--rotation R]` | Move a symbol property field. |
| `sch edit symbol add-pin <sch> <LIB_ID> <NUMBER> <NAME> <X,Y> --length L --type T [--rotation R] [--shape S] [--lib-file PATH]` | Add a pin to an embedded and optional standalone symbol. |
| `sch edit wire add <sch> <X1,Y1> <X2,Y2> [--type solid\|default]` | Add a wire segment. |
| `sch edit wire delete <sch> (--uuid U \| --from X,Y --to X,Y)` | Delete a wire. |
| `sch edit label add <sch> <KIND> <NAME> <X,Y> [--rotation R] [--justify J]` | Add a `global`, `hier`, or `local` label. |
| `sch edit label move <sch> <UUID> <X,Y> [--rotation R]` | Move a label. |
| `sch edit label delete <sch> <UUID>` | Delete a label. |
| `sch edit junction add <sch> <X,Y>` | Add a junction. |
| `sch edit junction delete <sch> (--uuid U \| --at X,Y)` | Delete a junction. |

## Decision Rules

- Need to know something? Use `sch query`.
- Need to change structure? Use `sch edit`.
- Need to check connectivity after an edit? Use `sch validate --baseline DIR`.
- Need visual confirmation? Use `sch render-region` with a tight bbox.
- Need score/collision data? Use `sch inspect`.

Pass the top-level schematic to `erc`, `netlist`, and `validate`. Pass the edited child sheet to `inspect` and `render-region`, or via `--sheet` for `validate`.

## JSON Shapes

`sch inspect` returns:

- `score`: nested object with `total`, `collision_count`, `wire_corner_count`, `symbol_wire_conflict_count`, and `symbol_wire_clearance_count`.
- `wire_length`: independent wire length metrics.
- `collisions`: text/label/property/wire/symbol collisions.
- `symbol_wire_conflicts`: body overlaps and clearance violations.
- `filters`: applied `only_text` and `margin`.

`sch validate` wraps stages as `{ "erc": {...}, "netlist": {...}, "inspect": {...} }`.

## Scripts

- `scripts/kicad_tool.py`: only executable entrypoint.
- `scripts/score_schematic.py`, `scripts/kicad_sch_bbox_collisions.py`, `scripts/text_collision_core.py`: import-only helpers.
- `scripts/sch_query.py`, `scripts/sch_edit.py`, `scripts/sch_netlist.py`: import-only helpers used by the entrypoint.
