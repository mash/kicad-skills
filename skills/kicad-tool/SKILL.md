---
name: kicad-tool
description: Use for ANY read or write on `.kicad_sch` / `.kicad_pcb` files — never `Read`/`cat`/`grep`/`Edit` them directly. Also use when the user asks what pins connect to what other pins, what net a pin is on, which components share a net, or the value/MPN/footprint of a component ref. All query, edit, render, inspect, ERC/DRC, netlist, and validate ops go through this CLI.
---

# KiCad Tool

Single owner of repository-local KiCad commands. Other skills must call this CLI, never the helper modules.

`.kicad_sch` / `.kicad_pcb` are huge S-expr files: raw `Read`/`cat` blows context and raw `Edit` corrupts UUIDs and `lib_symbols`. Pass the **top-level** schematic to `erc` / `netlist` / `validate`; pass the edited child sheet to `inspect` / `render-region` (or via `--sheet` for `validate`).

## Entrypoint

```bash
kicad-tool <domain> <command> ...
```

`KICAD_CLI` overrides the executable. Default: `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`.

### Bootstrap (first run)

Before any subcommand:

1. `command -v kicad-tool` — if found, skip the rest.
2. Otherwise inspect the host's Python (`which python3`, `pipx --version`, `uv --version`, `$VIRTUAL_ENV`, PEP 668 `EXTERNALLY-MANAGED`) and pick a fitting install method. Source URL is always `git+https://github.com/mash/kicad-skills.git` — this transitively pulls the [**`kiutils` fork**](https://github.com/mash/kiutils) (pinned in `pyproject.toml`; upstream lacks API used here). **Never `pip install kiutils`** — that grabs upstream and breaks the tool. Examples — pick one, don't run blindly:
   - `pipx install git+…` (preferred when available)
   - `uv tool install git+…`
   - `pip install git+…` inside an active venv
   - Full repo cloned for skill dev: `pip install -e .` from repo root
   - PEP 668-locked system Python: install `pipx` first instead of `--break-system-packages`
3. Confirm with the user before touching system Python or using `--break-system-packages`.
4. Re-check `command -v kicad-tool`. If still missing, fall back to `python3 -m kicad_tool <args>` and tell the user how to fix PATH (`pipx ensurepath`, etc.).

Verify: `kicad-tool --help`.

## Argument conventions

- `<schematic>` is always the first positional. Required args are positional in table order.
- Coordinates are a single `X,Y` (bbox: `X1,Y1,X2,Y2`); rotation is `--rotation R`.
- `--flag` only for optional knobs, mutex selectors (e.g. `--uuid` vs `--at` vs `--through` for `sch query wire`), and the universal `--dry-run` (edits) / `--format {json,text}` (all).
- **Output is text by default for every command.** Text and `--format json` carry **identical information** (text is YAML-like, JSON is JSON). Use `--format json` **only when piping to `jq` / a script**; for plain reading, stick with text.
- **On runtime failure** the tool prints the offending subcommand's `usage:` line to stderr after the error message. Read the error tail; do **not** invoke `--help` separately.
- **Do not invent flags for required args.** `--bbox`, `--ref`, `--symbol` etc. do not exist.

## Commands

| Command | Purpose | Key flags |
|---|---|---|
| `sch render-region <sheet> <X1,Y1,X2,Y2>` | Cropped PNG of a sheet bbox (mm). Use whenever the model needs to *see* part of a schematic. **Take the bbox wide enough to include neighboring symbols, rails, and labels — minimum-tight bbox loses the context needed to judge layout.** | `-o/--output <path>` (default `tmp/region.png`) |
| `sch inspect <sheet>` | Score + collisions (re-renders SVG internally) | `--only-text <regex>`, `--margin` |
| `sch erc <top>` | ERC, hierarchical | `-o/--output <path>` |
| `sch netlist <top>` | Netlist export | `-o/--output <path>` |
| `sch validate <top>` | erc + netlist + inspect(--sheet) in one call | `--sheet <edited>`, `--save-baseline DIR`, `--baseline DIR` |
| `pcb drc <board>` | PCB DRC | `-o/--output <path>`, `--schematic-parity` |
| `pcb validate <board> <top.kicad_sch>` | DRC + ref/footprint-lib parity vs schematic | `--save-baseline DIR`, `--baseline DIR` |
| `pcb render-region <board> <X1,Y1,X2,Y2>` | Cropped PNG of the board (default agent layer set) | `--layers F.Cu,F.SilkS,...`, `-o/--output <path>` |
| `pcb sync <board> <top.kicad_sch>` | Add missing footprints (5 mm staging grid); swap footprints whose schematic `Footprint` property changed; refresh footprint bodies whose layout has drifted from the library `.kicad_mod` (KiCad GUI's "Update Footprints from Library") — all preserving position, rotation, board side, schematic-link (`path`/`sheetname`/`sheetfile`), pad nets, and user-added properties (e.g. `MPN`/`LCSC`/`Manufacturer`); update each pad's `(net "...")` to match the schematic netlist; idempotent. Tracks/vias/zones are not touched — orphaned net names from pad rename are reported. | `-o/--output <path>`, `--dry-run` |

### PCB DRC in Codex sandbox

On macOS, `kicad-cli pcb drc` may abort inside Codex's normal sandbox before writing a report, even when the same command works in the user's terminal. If `kicad-tool pcb drc ... --format json` reports `returncode: -6`, empty stdout/stderr, and no report file, re-run the exact `kicad-tool pcb drc ...` command with `sandbox_permissions="require_escalated"` and a narrow `prefix_rule` such as `["kicad-tool", "pcb", "drc"]`. A successful DRC process can still print `DRC: exit=0` while the generated `.rpt` contains violations, because this wrapper does not pass KiCad's `--exit-code-violations`.

### Query (read-only)

Positional `<schematic.kicad_sch>`. Text by default; `--format json` only when piping to `jq`. Same content either way.

| Command | Purpose |
|---|---|
| `sch query symbol <sch> <REF>` | Symbol position, properties (Value/MPN/LCSC/Footprint), pin map |
| `sch query pin <sch> <REF.PIN> [--netlist PATH]` | Pin coords, orientation, and (with netlist) net |
| `sch query net <sch> <NAME_OR_REF.PIN> --netlist PATH` | Members and labels of a net (REF.PIN dispatched by `.`) |
| `sch query region <sch> <X1,Y1,X2,Y2>` | All elements inside a bbox |
| `sch query wire <sch> (--uuid U \| --at X,Y \| --through X,Y)` | Locate a wire |
| `sch query label <sch> (--name N \| --uuid U)` | Locate labels |
| `sch query lib-symbol <sch> <LIB_ID>` | Library symbol pins/units (large; prefer `query symbol <REF>` if a placed instance exists) |
| `sch query list <sch> <KIND> [--netlist PATH]` | KIND ∈ symbols/labels/wires/junctions/nets (`nets` requires `--netlist`) |

PCB equivalents (same shape):

| Command | Purpose |
|---|---|
| `pcb query footprint <board> <REF>` | Position, layer, rotation, properties, locked state, pad summary |
| `pcb query pad <board> <REF.PAD>` | Absolute pad geometry and net |
| `pcb query net <board> <NAME_OR_REF.PAD>` | Members of a net |
| `pcb query region <board> <X1,Y1,X2,Y2>` | Footprints/drawings/tracks/vias/zones in bbox |
| `pcb query list <board> <KIND>` | KIND ∈ footprints/tracks/vias/zones/drawings/nets |
| `pcb query zone <board> (--uuid U \| --name N \| --net NET --layer LAYER)` | Single zone: uuid, name, net, layer, priority, settings, polygon points, bbox, `area_mm2` (shoelace outline area, mm²). Mutex selectors; `--layer` required with `--net` |
| `pcb query via <board> (--uuid U \| --at X,Y [--tolerance MM])` | Single via: uuid, at, size, drill, layers, net, free, locked. `--at` returns the unique via within tolerance (default 0.05 mm); multiple matches return `ambiguous` with `candidates` |

### Edit (structural mutations)

Positional `<schematic.kicad_sch>` / `<board.kicad_pcb>`. Text summary by default (dry-run also prints a unified diff); supports `--dry-run`. `--format json` for the structured payload.

| Command | Purpose |
|---|---|
| `sch edit symbol add <sch> <LIB_ID> <REF> <X,Y> [--lib-file PATH]` | Add a symbol instance. If `<LIB_ID>` is already in the schematic's embedded `(lib_symbols ...)`, clones an existing same-`lib_id` sibling (must be annotated, unmirrored, unit-1) and inherits its Value/Footprint/rotation/`on_board`/`in_bom`. Otherwise, with `--lib-file <library.kicad_sym>`, imports the symbol definition into `(lib_symbols ...)` and synthesizes a fresh instance (works for pinless symbols like `Mechanical:MountingHole` and for top schematics with empty `(lib_symbols)`). Adjust Value/Footprint/attributes afterwards with `set-property` / `set-attribute`. |
| `sch edit symbol move <sch> <REF> <X,Y> [--rotation R]` | Move/rotate a symbol |
| `sch edit symbol move-property <sch> <REF> <KEY> <X,Y> [--rotation R]` | Move a property field |
| `sch edit symbol add-pin <sch> <LIB_ID> <NUMBER> <NAME> <X,Y> --length L --type T [--rotation R] [--shape S] [--lib-file PATH]` | Add a pin (updates embedded lib_symbols + `.kicad_sym`) |
| `sch edit symbol delete <sch> <REF>` | Delete a symbol; reports connected taps but does **not** remove them |
| `sch edit symbol set-property <sch> <REF> <KEY> <VALUE>` | Set any property (built-in or user-defined: MPN/LCSC/Manufacturer/...) |
| `sch edit symbol set-attribute <sch> <REF> <in_bom\|on_board> <yes\|no>` | Set a schematic symbol boolean attribute; use `on_board no` for off-board BOM parts |
| `sch edit wire add <sch> <X1,Y1> <X2,Y2> [--type solid\|default]` | Add a wire segment |
| `sch edit wire delete <sch> <UUID>` | Delete a wire |
| `sch edit label add <sch> <KIND> <NAME> <X,Y> [--rotation R]` | KIND ∈ global/hier/local |
| `sch edit label move <sch> <UUID> <X,Y> [--rotation R]` | Move a label |
| `sch edit label delete <sch> <UUID>` | Delete a label |
| `sch edit junction add <sch> <X,Y>` | Add a junction |
| `sch edit junction delete <sch> <UUID>` | Delete a junction |
| `pcb edit footprint move <board> <REF> <X,Y> [--rotation R]` | Move/rotate a footprint |
| `pcb edit footprint move-property <board> <REF> <KEY> <X,Y> [--rotation R]` | Move a property field |
| `pcb edit footprint set-property <board> <REF> <KEY> <VALUE>` | Set a property (refuses `Reference` — use schematic + `pcb sync`) |
| `pcb edit footprint move-layer <board> <REF> front\|back [--at X,Y] [--rotation R]` | Flip to F.Cu / B.Cu (mirrors geometry, preserves property positions) |
| `pcb edit zone set-polygon <board> (--uuid U \| --name N \| --net NET --layer LAYER) <X1,Y1> <X2,Y2> <X3,Y3> [...]` | Replace a zone's outline polygon (≥3 points). Strips stale `(filled_polygon ...)` (KiCad refills). Rejects degenerate polygons (zero area, consecutive duplicate points) |
| `pcb edit zone add <board> <NET> <LAYER> <X1,Y1> <X2,Y2> <X3,Y3> [...] --copy-settings-from-uuid U` | Add a new zone, inheriting settings (hatch/connect_pads/clearance/min_thickness/fill/thermal) from an existing zone. Net and layer must already exist. `--name`, `--priority` optional overrides. Secondary path: explicit settings flags instead of `--copy-settings-from-uuid` |
| `pcb edit zone delete <board> (--uuid U \| --name N \| --net NET --layer LAYER)` | Delete a zone block entirely. Tracks/vias/other zones untouched |
| `pcb edit zone set-property <board> (--uuid U \| --name N) <priority\|clearance\|min_thickness\|thermal_gap\|thermal_bridge_width\|name> <VALUE>` | Edit a single zone setting (KEY whitelisted). Strips `(filled_polygon ...)` after mutation |
| `pcb edit via add <board> <NET> <X,Y> [--size MM] [--drill MM] [--layers F.Cu,B.Cu] [--free]` | Add a new via. Net must exist. Defaults size/drill from board `(setup)` (`via_size`/`via_drill`), else 0.8/0.4. UUID is deterministic from (net,xy,layers) — re-adding the same triple is idempotent |
| `pcb edit via delete <board> (--uuid U \| --at X,Y [--tolerance MM])` | Delete a single via by uuid or nearest position. Multiple candidates within tolerance → error |
| `pcb edit via move <board> (--uuid U \| --at X,Y [--tolerance MM]) <NEW_X,NEW_Y>` | Move a via. Only `(at ...)` is rewritten; size/drill/net/layers/uuid unchanged |
| `pcb edit via set-property <board> (--uuid U \| --at X,Y [--tolerance MM]) <size\|drill\|net\|layers\|free\|locked> <VALUE>` | Set a single via field (KEY whitelisted). `layers` is comma-separated; `free`/`locked` accept yes/no/true/false/0/1; `net` is name-validated |

Locked footprints are refused. Only the targeted block is rewritten; UUIDs and surrounding formatting are preserved.

## Inspect / Validate JSON

`sch inspect` (and `sch validate`'s nested `inspect` key) returns:

- `score` — **nested object**, not a number. Access as `score.total`, `score.collision_count`, etc. Sub-fields:
  - `total` (lower better), `collision_count`
  - `wire_hits`, `component_hits`, `text_hits`, `collision_area`
  - `wire_corner_count` / `wire_corner_penalty` (two-segment bends + wire-endpoint T-junctions)
  - `diagonal_wire_count` / `diagonal_wire_penalty`
  - `symbol_wire_conflict_count` / `symbol_wire_penalty` (wires crossing symbol bodies)
  - `symbol_wire_clearance_count` / `symbol_wire_clearance_penalty` (wires too close to symbols)
  - `symbol_wire_edge_hug_count` / `symbol_wire_edge_hug_penalty`
- `wire_length` — total length, segment count, longest segment (non-score metric).
- `collisions` — text/label/property/wire/symbol records.
- `symbol_wire_conflicts` — body overlaps + clearance violations.
- `filters` — applied `only_text`, `margin`.

`sch validate` wraps three stages: `{ "erc": {...}, "netlist": {...}, "inspect": {...} }`. Read score/collisions through `d["inspect"]["score"]`, **not** the top level.

## Scripts

- `scripts/kicad_tool.py` — module behind the `kicad-tool` console script (do not invoke directly).
- `scripts/score_schematic.py`, `scripts/kicad_sch_bbox_collisions.py`, `scripts/text_collision_core.py` — import-only helpers.
