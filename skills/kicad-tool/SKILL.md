---
name: kicad-tool
description: Use for ANY read or write on `.kicad_sch` / `.kicad_pcb` / `.kicad_pro`. Do NOT `Read`/`cat`/`grep`/`Edit` these files — use `sch query` / `pcb query` (what is REF connected to, its Value/MPN/footprint, where it sits, what's in a region/net) and `sch edit` / `pcb edit` (move/add/delete symbols, wires, labels, junctions; set properties; move footprints). Also: render-region, inspect/score, ERC, netlist, validate (with baseline diff), PCB DRC, footprint import. Missing op → extend this tool, do not fall back to raw file ops.
---

# KiCad Tool

Single owner of repository-local KiCad commands. Other skills must call this CLI, never the helper modules.

## When to use (instead of Read/Edit/grep)

`.kicad_sch` / `.kicad_pcb` are huge S-expr files. Raw `Read`/`cat` blows context; raw `Edit` corrupts UUIDs, formatting, and `lib_symbols`. Use this CLI; if the op is missing, extend it.

Reads (use `sch query` / `pcb query`, never `Read`/`cat`/`grep`):

- net of a pin → `sch query pin <sch> REF.PIN --netlist N.json`
- members of a net → `sch query net <sch> NAME --netlist N.json`
- properties / placement of a symbol (Value, MPN, LCSC, Footprint, x/y/rot) → `sch query symbol <sch> REF`
- what's at coords / in a bbox → `sch query region <sch> X1,Y1,X2,Y2`
- list all of one kind → `sch query list <sch> {symbols,labels,wires,junctions,nets}`
- lib_symbol pins/units → `sch query lib-symbol <sch> <LIB_ID>`
- PCB equivalents → `pcb query {footprint,pad,net,region,list}`

Writes (use `sch edit` / `pcb edit`, never `Edit`/`Write`):

- set Value / MPN / LCSC / any property → `sch edit symbol set-property <sch> REF KEY VALUE`
- move / rotate / delete a symbol → `sch edit symbol {move,delete} ...`
- wire / label / junction add/delete → `sch edit {wire,label,junction} {add,delete} ...`
- new pin on a custom symbol → `sch edit symbol add-pin ...`
- footprint move/flip → `pcb edit footprint {move,move-property,set-property,move-layer} ...`

Required args are positional — do not invent `--ref` / `--bbox` / `--symbol`.

## Entrypoint

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/kicad-tool/scripts/kicad_tool.py <domain> <command> ...
```

`KICAD_CLI` overrides the executable. Default: `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`.

Python deps (kiutils fork) are declared in this plugin's `pyproject.toml`. Install once with `pip install <path-to-kicad-skills-repo>` (or `pip install .` from the repo root).

## Argument conventions

- `<schematic>` is always the first positional.
- Required args are positional, in table order. Coordinates are a single `X,Y` (bbox: `X1,Y1,X2,Y2`); rotation is `--rotation R`.
- `--flag` only for optional knobs, mutex selectors (e.g. `--uuid` vs `--at` vs `--through` for `sch query wire`), and the universal `--dry-run` (edits) / `--format {json,text}` (all).
- **Output is text by default for every command.** Pass `--format json` to get the structured payload (machine consumption / detailed query data).
- **On runtime failure** the tool prints the offending subcommand's `usage:` line to stderr after the error message. Read the error tail; do **not** invoke `--help` separately.
- **Do not invent flags for required args.** `--bbox`, `--ref`, `--symbol` etc. do not exist; bbox/ref/coords are positional.

## Commands

| Command | Purpose | Key flags |
|---|---|---|
| `sch render-region <sheet> <X1,Y1,X2,Y2>`<br>e.g. `... rx-power.kicad_sch 88,80,115,130` | Render a cropped PNG of a bbox region of a sheet (mm). Use this whenever the model needs to *see* part of a schematic — image cost is bounded, SVG text isn't. (Humans wanting to view a full sheet should open KiCad directly.) | `--output <path>` (default `tmp/region.png`) |
| `sch inspect <sheet>` | Score + collisions (re-renders SVG internally) | `--only-text <regex>` (Python regex; score stays global), `--margin` |
| `sch erc <top>` | ERC, hierarchical | `--report <path>` |
| `sch netlist <top>` | Netlist export | `--output <path>` |
| `sch validate <top>` | erc + netlist + inspect(--sheet) in one call | `--sheet <edited>` |
| `pcb drc <board>` | PCB DRC | `--report <path>`, `--schematic-parity` |
| `pcb validate <board> <top.kicad_sch>` | DRC + ref/footprint-lib parity vs schematic | `--save-baseline DIR`, `--baseline DIR` |
| `pcb render-region <board> <X1,Y1,X2,Y2>` | Cropped PNG of the board (default agent layer set) | `--layers F.Cu,F.SilkS,...`, `--output <path>` |
| `pcb import-footprints <board> <top.kicad_sch>` | Splice missing schematic footprints into the board on a 5 mm staging grid; never touches existing footprints | `--output <path>`, `--dry-run` |

### Query

Read-only inspection of a sheet. All take a positional `<schematic.kicad_sch>` and emit a text summary by default (key/value lines and counts); pass `--format json` for the full structured payload.

| Command | Purpose |
|---|---|
| `sch query symbol <sch> <REF>`<br>e.g. `... rx-power.kicad_sch C7` | Symbol details (position, properties, pin map) by reference |
| `sch query pin <sch> <REF.PIN> [--netlist PATH]`<br>e.g. `... rx-power.kicad_sch C7.1` | One pin's coordinates, orientation, and (with netlist) net |
| `sch query net <sch> <NAME_OR_REF.PIN> --netlist PATH` | Members and labels of a net (REF.PIN dispatched by `.`) |
| `sch query region <sch> <X1,Y1,X2,Y2>` | All elements inside a bounding box |
| `sch query wire <sch> (--uuid U \| --at X,Y \| --through X,Y)` | Locate a wire by uuid, endpoint, or any point on the segment |
| `sch query label <sch> (--name N \| --uuid U)` | Locate labels by name or uuid |
| `sch query lib-symbol <sch> <LIB_ID>` | Library symbol definition (pins, units) |
| `sch query list <sch> <KIND> [--netlist PATH]` | Enumerate elements of one kind (KIND ∈ symbols/labels/wires/junctions/nets; `nets` requires `--netlist`) |

### Edit

Structural mutations. All take a positional `<schematic.kicad_sch>`, emit a text summary by default (action / changed / details; dry-run also prints a unified diff), and support `--dry-run`. Pass `--format json` for the structured payload.

| Command | Purpose |
|---|---|
| `sch edit symbol move <sch> <REF> <X,Y> [--rotation R]`<br>e.g. `... rx-power.kicad_sch C7 100,95 --rotation 90` | Move/rotate a symbol |
| `sch edit symbol move-property <sch> <REF> <KEY> <X,Y> [--rotation R]` | Move a symbol property field's placement |
| `sch edit symbol add-pin <sch> <LIB_ID> <NUMBER> <NAME> <X,Y> --length L --type T [--rotation R] [--shape S] [--lib-file PATH]` | Add a pin to a symbol (updates embedded lib_symbols + standalone .kicad_sym) |
| `sch edit symbol delete <sch> <REF>` | Delete a symbol by reference; reports connected wires/labels/junctions in `details.connected_taps` but does **not** remove them |
| `sch edit symbol set-property <sch> <REF> <KEY> <VALUE>` | Set a symbol property value; any key (built-ins or user-defined like `MPN`/`LCSC`/`Manufacturer`) |
| `sch edit wire add <sch> <X1,Y1> <X2,Y2> [--type solid\|default]` | Add a wire segment |
| `sch edit wire delete <sch> --uuid U` | Delete a wire |
| `sch edit label add <sch> <KIND> <NAME> <X,Y> [--rotation R]` | Add a label (KIND ∈ global/hier/local); for passive global labels, `(justify left/right)` follows rotation |
| `sch edit label move <sch> <UUID> <X,Y> [--rotation R]` | Move a label |
| `sch edit label delete <sch> <UUID>` | Delete a label |
| `sch edit junction add <sch> <X,Y>` | Add a junction |
| `sch edit junction delete <sch> --uuid U` | Delete a junction |

### PCB Query

Read-only inspection of a `.kicad_pcb`. All take a positional `<board.kicad_pcb>` and emit a text summary by default; pass `--format json` for the structured payload.

| Command | Purpose |
|---|---|
| `pcb query footprint <board> <REF>` | Position, layer, rotation, properties, locked state, pad summary |
| `pcb query pad <board> <REF.PAD>` | Absolute pad geometry and net (back-side mirror handled) |
| `pcb query net <board> <NAME_OR_REF.PAD>` | Members of a net (REF.PAD dispatched only when both ref and pad exist) |
| `pcb query region <board> <X1,Y1,X2,Y2>` | All footprints/drawings/tracks/vias/zones intersecting the bbox |
| `pcb query list <board> <KIND>` | Enumerate KIND ∈ footprints/tracks/vias/zones/drawings/nets |

### PCB Edit

Footprint placement mutations. All take a positional `<board.kicad_pcb>`, emit a text summary by default, and support `--dry-run`. Pass `--format json` for the structured payload.

| Command | Purpose |
|---|---|
| `pcb edit footprint move <board> <REF> <X,Y> [--rotation R]` | Move/rotate a footprint |
| `pcb edit footprint move-property <board> <REF> <KEY> <X,Y> [--rotation R]` | Move a footprint property field |
| `pcb edit footprint set-property <board> <REF> <KEY> <VALUE>` | Set a footprint property's string value (key must already exist) |
| `pcb edit footprint move-layer <board> <REF> front\|back [--at X,Y] [--rotation R]` | Flip a footprint to F.Cu / B.Cu (mirrors geometry, preserves property positions) |
| `pcb edit footprint set-property <board> <REF> <KEY> <VALUE>` | Set a footprint property value (Value/MPN/LCSC/Manufacturer/etc); refuses `Reference` (use schematic + `pcb import-footprints` instead) |

Locked footprints are refused. Only the targeted footprint block is rewritten; UUIDs and surrounding formatting are preserved.

### Validate (with baseline)

| Command | Purpose |
|---|---|
| `sch validate <top> --save-baseline DIR` | Save current ERC + netlist as the baseline |
| `sch validate <top> --baseline DIR` | Re-run validation and diff against the saved baseline |

### Decision rules

- "I want to know X" → `sch query`. Pick the element by what you have a name/coord for: a `REF` → `symbol`; `REF.PIN` → `pin`; a net name → `net`; coords → `region` or `wire`/`junction`/`label` by `--at`.
- "I want to change something structural" → `sch edit <element> <action>`. The action is `add` / `move` / `delete`.
- "I want to know if my edit broke connectivity" → `sch validate --baseline DIR`.
- "I want to render or score" → `sch inspect` (score) / `sch render-region` (cropped image).
- "I want to visually confirm a small edit" → `sch render-region` with a tight bbox (or run `sch query symbol REF` first to grab coordinates). Do **not** `Read` the full sheet SVG — the XML is huge and burns context for no gain.

Pass the **top-level** schematic to `erc` / `netlist` / `validate`. Pass the edited child sheet to `inspect` / `render-region` (or via `--sheet` for `validate`).

Example — full validation after editing a child sheet:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/kicad-tool/scripts/kicad_tool.py sch validate \
  <top.kicad_sch> --sheet <edited-child.kicad_sch>
```

## Inspect JSON

`sch inspect` returns (JSON by default) a top-level object with these keys:

- `score` — **a nested object**, not a number. Sub-fields (access as `score.total`, `score.collision_count`, etc.):
  - `total` (lower better)
  - `collision_count`
  - `wire_corner_count` (two-segment bends + wire-endpoint T-junctions)
  - `symbol_wire_conflict_count` (wires crossing symbol bodies)
  - `symbol_wire_clearance_count` (wires too close to symbols)
- `wire_length` — independent non-score metric with total wire length, segment count, and longest segment.
- `collisions` — list of text/label/property/wire/symbol records.
- `symbol_wire_conflicts` — list of body overlaps + clearance violations.
- `filters` — applied `only_text`, `margin`.

## Validate JSON

`sch validate` wraps the three stages: `{ "erc": {...}, "netlist": {...}, "inspect": { <same shape as sch inspect> } }`. To read `score` / `collisions` / `symbol_wire_conflicts`, always go through the nested `inspect` key (e.g. `d["inspect"]["score"]`) — they are **not** at the top level.

## Scripts

- `scripts/kicad_tool.py` — only executable entrypoint.
- `scripts/score_schematic.py`, `scripts/kicad_sch_bbox_collisions.py`, `scripts/text_collision_core.py` — import-only helpers; do not invoke directly.
