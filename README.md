# KiCad Skills

AI-agent skills and scripts for working with KiCad schematic and PCB design files.

## Skills

### `kicad-tool`

Single CLI (`kicad-tool`) that owns every read and write on `.kicad_sch` / `.kicad_pcb` files. Agents must route all query, edit, render, inspect, ERC/DRC, netlist, and validate operations through this tool instead of `Read`/`cat`/`grep`/`Edit` (raw access blows context and corrupts UUIDs / `lib_symbols`).

### `kicad-workflow`

Stage-1 **functional** editing process. Plans, sequences, and validates structural changes — adding/removing/modifying symbols, wires, labels, footprints — including dry-run, baseline diff, ERC, netlist, and DRC. Pairs with `kicad-tool` for the actual commands.

### `kicad-sch-cleanup-loop`

Stage-2 **visual** cleanup loop. Iteratively improves a schematic sheet's `sch inspect` score (collisions, wire bends, symbol clearance) without changing connectivity. Subagents edit one cluster at a time; the main loop scores and plans.

## Layout

```text
skills/
  kicad-tool/
  kicad-workflow/
  kicad-sch-cleanup-loop/
```

## Usage

Ask your coding agent to import either the whole repository or a single skill folder into its local skill/search path.

Whole repository:

```text
Import https://github.com/mash/kicad-skills as local KiCad skills for this project.
Use all skills under skills/ when working with KiCad schematic or PCB files.
```

Single skill:

```text
Import https://github.com/mash/kicad-skills/tree/main/skills/kicad-tool as a local skill.
Use it whenever you need to query, edit, render, or validate KiCad files.
```

For agents that do not manage skills automatically, copy the desired folders under `skills/` into the agent's local skill directory. Keep each skill folder intact, including its `SKILL.md`, `agents/`, and `scripts/` files.

## Example prompts

After import, drive the skills with prompts like:

```text
Use the KiCad skills to inspect this schematic sheet and summarize the current ERC/netlist status.
```

```text
Use the KiCad workflow skill to make this schematic edit safely, including dry-run, validation, and a concise report.
```

```text
Use the KiCad schematic cleanup loop to improve the visual score of this sheet without changing connectivity.
```

```text
What net is U3.7 on? Use kicad-tool.
```

```text
Render the area around U1 on sheet power.kicad_sch so I can see the decoupling layout.
```

## `kicad-tool` command reference

Entrypoint: `kicad-tool <domain> <command> ...`. Text output by default; pass `--format json` for structured payloads. `<schematic>` / `<board>` are always the first positional. Coordinates are `X,Y` (bbox: `X1,Y1,X2,Y2`). `KICAD_CLI` overrides the KiCad executable.

### Schematic — top-level operations

| Command | Purpose |
|---|---|
| `sch render-region <sheet> <X1,Y1,X2,Y2>` | Cropped PNG of a sheet bbox (mm). Take the bbox wide to include neighbors/labels. `-o/--output` (default `tmp/region.png`) |
| `sch inspect <sheet>` | Layout score + collisions; `--only-text <regex>`, `--margin` |
| `sch erc <top>` | Hierarchical ERC; `-o/--output` |
| `sch netlist <top>` | Netlist export; `-o/--output` |
| `sch validate <top>` | erc + netlist + inspect in one call; `--sheet <edited>`, `--save-baseline DIR`, `--baseline DIR` |

### Schematic — query (read-only)

| Command | Purpose |
|---|---|
| `sch query symbol <sch> <REF>` | Symbol position, properties (Value/MPN/LCSC/Footprint), pin map |
| `sch query pin <sch> <REF.PIN> [--netlist PATH]` | Pin coords, orientation, and (with netlist) net |
| `sch query net <sch> <NAME_OR_REF.PIN> --netlist PATH` | Members and labels of a net |
| `sch query region <sch> <X1,Y1,X2,Y2>` | All elements inside a bbox |
| `sch query wire <sch> (--uuid U \| --at X,Y \| --through X,Y)` | Locate a wire |
| `sch query label <sch> (--name N \| --uuid U)` | Locate labels |
| `sch query lib-symbol <sch> <LIB_ID>` | Library symbol pins/units |
| `sch query list <sch> <KIND> [--netlist PATH]` | KIND ∈ symbols/labels/wires/junctions/nets (`nets` requires `--netlist`) |

### Schematic — edit (mutations; `--dry-run` supported)

| Command | Purpose |
|---|---|
| `sch edit symbol move <sch> <REF> <X,Y> [--rotation R]` | Move/rotate a symbol |
| `sch edit symbol move-property <sch> <REF> <KEY> <X,Y> [--rotation R]` | Move a property field |
| `sch edit symbol add <sch> <LIB_ID> <REF> <X,Y>` | Add a new instance by cloning an existing same-`lib_id` sibling |
| `sch edit symbol add-pin <sch> <LIB_ID> <NUMBER> <NAME> <X,Y> --length L --type T [--rotation R] [--shape S] [--lib-file PATH]` | Add a pin (updates embedded `lib_symbols` + `.kicad_sym`) |
| `sch edit symbol delete <sch> <REF>` | Delete a symbol; reports connected taps but does not remove them |
| `sch edit symbol set-property <sch> <REF> <KEY> <VALUE>` | Set any property (built-in or user-defined: MPN/LCSC/Manufacturer/...) |
| `sch edit symbol set-attribute <sch> <REF> <in_bom\|on_board> <yes\|no>` | Set a symbol boolean attribute (e.g. `on_board no` for off-board BOM parts) |
| `sch edit wire add <sch> <X1,Y1> <X2,Y2> [--type solid\|default]` | Add a wire segment |
| `sch edit wire delete <sch> --uuid U` | Delete a wire |
| `sch edit label add <sch> <KIND> <NAME> <X,Y> [--rotation R]` | KIND ∈ global/hier/local |
| `sch edit label move <sch> <UUID> <X,Y> [--rotation R]` | Move a label |
| `sch edit label delete <sch> <UUID>` | Delete a label |
| `sch edit junction add <sch> <X,Y>` | Add a junction |
| `sch edit junction delete <sch> --uuid U` | Delete a junction |

### PCB — top-level operations

| Command | Purpose |
|---|---|
| `pcb drc <board>` | PCB DRC; `-o/--output`, `--schematic-parity` |
| `pcb validate <board> <top.kicad_sch>` | DRC + ref/footprint-lib parity vs schematic; `--save-baseline DIR`, `--baseline DIR` |
| `pcb render-region <board> <X1,Y1,X2,Y2>` | Cropped PNG (default agent layer set); `--layers F.Cu,F.SilkS,...`, `-o/--output` |
| `pcb sync <board> <top.kicad_sch>` | Add missing footprints (5 mm staging grid) AND update each pad's `(net "...")` to match the schematic netlist; idempotent; tracks/vias/zones are not touched — orphaned net names are reported; `-o/--output`, `--dry-run` |

### PCB — query (read-only)

| Command | Purpose |
|---|---|
| `pcb query footprint <board> <REF>` | Position, layer, rotation, properties, locked state, pad summary |
| `pcb query pad <board> <REF.PAD>` | Absolute pad geometry and net |
| `pcb query net <board> <NAME_OR_REF.PAD>` | Members of a net |
| `pcb query region <board> <X1,Y1,X2,Y2>` | Footprints/drawings/tracks/vias/zones in bbox |
| `pcb query list <board> <KIND>` | KIND ∈ footprints/tracks/vias/zones/drawings/nets |

### PCB — edit (mutations; `--dry-run` supported)

| Command | Purpose |
|---|---|
| `pcb edit footprint move <board> <REF> <X,Y> [--rotation R]` | Move/rotate a footprint |
| `pcb edit footprint move-property <board> <REF> <KEY> <X,Y> [--rotation R]` | Move a property field |
| `pcb edit footprint set-property <board> <REF> <KEY> <VALUE>` | Set a property (refuses `Reference` — use schematic + `pcb sync`) |
| `pcb edit footprint move-layer <board> <REF> front\|back [--at X,Y] [--rotation R]` | Flip to F.Cu / B.Cu (mirrors geometry, preserves property positions) |
| `pcb edit footprint delete <board> <REF>` | Delete a footprint by reference |

Locked footprints are refused. Only the targeted block is rewritten; UUIDs and surrounding formatting are preserved. See `skills/kicad-tool/SKILL.md` for the inspect/validate JSON shape and other details.

## Dependencies

The bundled scripts require the `kiutils` fork published at `https://github.com/mash/kiutils`.

Your AI agent should guide you through dependency installation when it first needs to run the bundled scripts. The manual command is:

```bash
pip install .
```

This declares the kiutils fork dependency and exposes the `kicad-tool` console script.

## Scope

This repository intentionally contains KiCad file/tool/workflow skills only. Project-specific electrical design rules, board constraints, and product decisions should live in the consuming repository.

## Status

Early extraction from an active KiCad hardware workflow. APIs and skill wording may change while the plugin is being hardened.
