---
name: kicad-workflow
description: Stage 1 (functional) workflow for editing and validating KiCad hardware design files. Owns the editing loop — `sch edit` mechanics, dry-run/apply, plan-and-approval gate with ASCII layout sketch, ERC/netlist/inspect validation, baseline diffs, and PCB DRC after board changes. Pair with `kicad-tool` for exact commands; design judgement (invariants, partitioning, decoupling) belongs to repo-local skills like `electronics-design-principles`. Score/visual cleanup (stage 2) lives in `kicad-sch-cleanup-loop`.
---

# KiCad Workflow

Editing **workflow** for KiCad hardware files. This skill owns the _how_; design _what/why_ lives in the consuming repository (e.g. `electronics-design-principles`) and project hardware docs.

For any KiCad CLI invocation (render, inspect, ERC, netlist, validate, DRC), **read the `kicad-tool` skill via the Skill tool first** to get exact subcommands and flags.

Project-specific facts — top schematic path, sheet inventory, board stackup, electrical partitioning, placement rules — belong in the consuming repository's `hardware/` docs. Read them before choosing what to change.

## File types

| File | Approach |
|---|---|
| `.kicad_sch` | Use `sch query` and `sch edit` via `kicad-tool`. Preserve KiCad 10 formatting and UUIDs. |
| `.kicad_pcb` | Use `pcb edit` via `kicad-tool` when available; otherwise minimal local S-expression edits. |
| `.kicad_pro` | JSON edit only when needed. Minimal. |
| `.kicad_prl` | Do not edit unless explicitly asked. |
| `.kicad_sym` | Keep standalone library in sync with the schematic's embedded `lib_symbols` when changing custom symbols. |

## Editing rules

- For automated cases (move/add/delete symbol, wire, label, junction; add-pin; set-property), use `sch edit ...` / `pcb edit ...` — see `kicad-tool`. Always pass `--dry-run` first, then re-run without it. Validate after every edit.
- Adding pins: `sch edit symbol add-pin` updates both the schematic's embedded `lib_symbols` and the project's `.kicad_sym` library together. Coordinates are symbol-local.
- Do not `Read`/`cat`/`grep` `.kicad_sch` / `.kicad_pcb` raw — S-expression files are huge and burn the context window. Use `sch query` / `pcb query` for scoped output.
- If `sch edit` / `pcb edit` lacks a subcommand for a write operation, **extend kicad-tool first**. Raw `Edit`/`Write` on `.kicad_sch` / `.kicad_pcb` is the last resort and requires explicit user approval.

### Wire delete / replace — audit mid-span taps first

A wire can carry connections **anywhere along its length** (junctions, perpendicular wire endpoints, pin endpoints, label anchors), not only at its two endpoints. Endpoint-only stub replacement orphans mid-span taps; ERC misses it (a remaining junction at the orphaned coordinate keeps the count parity-clean).

Before any `sch edit wire delete` / replace:

1. Query the wire bbox with about 0.01 mm margin (`sch query region`).
2. Enumerate every tap: wire endpoints, junctions, perpendicular endpoints, pin endpoints, label anchors.
3. Ensure the replacement reconnects each tap.
4. Validate with the baseline-diff workflow below — its `removed_nodes` check is what actually catches this.

## Stage 1 workflow

This skill is **stage 1: functional editing**. Visual/score cleanup is stage 2 (`kicad-sch-cleanup-loop`) — finish the functional edit and validate it here, then hand off if score improvement is wanted.

### 0. Plan and get approval (mandatory)

Before the approval plan, **bird's-eye self-check, three zoom levels** — one sentence each (for you; surface anything load-bearing):

- **Board**: does this change fit the project's critical path now, or should it wait?
- **Sheet**: what function does the region serve, and will the change ripple style-wise into a follow-up pass elsewhere?
- **Local**: what does the area look like when done; what alternative framing might the request really mean (different scope/mechanism)?

Then state and wait for approval:

1. **Goal** — what the design should do (design terms, not "add wires").
2. **Problem** — what's missing/wrong, citing specific symbols/nets/pins (via `sch query`).
3. **Approach** — proposed change + alternatives considered + why this one. Smallest unit that meets the goal.
4. **Placement** — for each new/moved symbol, which existing symbol or net it should sit *near* and why. Apply the project's electrical-design rules (grouping, partitioning, decoupling near supply pins, function-grouped).
5. **Layout sketch (ASCII art) — required** — show **both** the *current* local layout and the *proposed* layout side-by-side (or before/after) as small ASCII diagrams. Include anchor symbols/pins, the new/moved parts, and the connecting wires/labels. Coordinates (X,Y numbers) are *supplementary* to the sketch — the sketch is the primary artifact for the user to spot orientation/topology issues before any edit runs. Approximate proportions are fine; what matters is relative position and connectivity. Example:

   ```
   Current:                Proposed:
        VCC                     VCC
         │                       │
         ●── U2.VCC               ●── U2.VCC
                                  │
                                 ─┴─ C99 100nF
                                  │
                                  ●── GND
   ```

Skip only if (a) the user gave an exact single action ("move R5 to 100,50"), or (b) running under an autonomous-iteration context the user explicitly invoked (e.g. `/kicad-sch-cleanup-loop`). When in doubt, plan and wait.

### 1. Pre-edit baseline

For routine schematic edits, the one-shot validate is enough:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/kicad-tool/scripts/kicad_tool.py sch validate \
  <top.kicad_sch> --sheet <edited-child.kicad_sch>
```

This runs ERC + netlist + `sch inspect` in order, stopping on the first failure.

For non-trivial structural edits (wire delete/replace, hierarchy changes), snapshot a baseline first so you can confirm connectivity is preserved — not just "ERC still passes":

```bash
# Before edit:
python3 ${CLAUDE_PLUGIN_ROOT}/skills/kicad-tool/scripts/kicad_tool.py sch validate \
  <top.kicad_sch> --save-baseline tmp/baseline

# After edit:
python3 ${CLAUDE_PLUGIN_ROOT}/skills/kicad-tool/scripts/kicad_tool.py sch validate \
  <top.kicad_sch> --baseline tmp/baseline
```

`--save-baseline DIR` writes `erc.rpt`, `netlist.net`, `inspect.json` to `DIR`. `--baseline DIR` reruns the stages and emits a JSON diff (`added_nets` / `removed_nets` / `changed_nets` / `new_erc_errors`); exits non-zero on regression (new ERC errors, removed nets, or removed nodes from existing nets).

### 2. Edit (functional)

Smallest change satisfying the request. Pin additions: keep embedded `lib_symbols` and the project's `.kicad_sym` library in sync.

Scope of stage 1: connectivity, nets, symbols, pins, values, hierarchy, ERC correctness. Do **not** chase `sch inspect` score, collisions, `wire_corner_count`, `diagonal_wire_penalty`, or label/value tightening here — that is stage 2.

If the new edit happens to introduce obvious local mess (overlapping label, crossing wire) that would block reading the diff, fix only that local mess. Anything broader belongs to the cleanup loop.

### 3. Validate

Re-run the validate / baseline command from step 1. Target rules when running stages individually:

- **ERC**: always against the **top-level** schematic (hierarchical ERC propagates into children). Never on a child sheet directly.
- **Netlist**: always from the top-level.
- **Inspect**: each edited sheet (re-renders SVG internally for scoring).
- **PCB DRC**: only when the board was edited; pass `--schematic-parity` for parity check.

### 4. Review

- Existing ERC violations are not failures; **new** ones are. Intentional new violations (e.g. a deliberately-dangling test label) are acceptable — call them out in the report.
- Netlist diff: visual-only edits should differ only in path/header metadata. Electrical edits — summarize the intended changes.
- Successful CLI exit ≠ correctness. **ERC parity + expected netlist diff + score check (when relevant)** = high confidence.
- `git diff` review: confirm `Sheetfile` references, net names, and that custom symbol changes appear in both embedded `lib_symbols` and the standalone `.kicad_sym`.
- A worse `sch inspect` score is acceptable in stage 1 if connectivity is correct; do not chase it here.

### 5. Hand off to stage 2 (visual cleanup)

Stage 1 ends once ERC and netlist are correct. If the user wants score/layout tightening on the edited sheet, hand off to `kicad-sch-cleanup-loop` with the sheet path. Do not perform score-driven layout work in this skill.

## Board changes

For `.kicad_pcb` changes:

- Keep stable: `layers`, `setup`, `setup.stackup`, `Edge.Cuts`, title block — unless the task explicitly targets them.
- High-risk fields: `net`, `footprint`, `segment`, `via`, `zone`.
- Run `pcb drc` after board edits.
- For schematic/board parity, use the `--schematic-parity` mode of `pcb drc`.
- Document known KiCad CLI limitations separately from true DRC violations.

Project-specific stackup and domain-separation rules live in the consuming repository's `hardware/pcb/` docs.

## Stage boundary

This workflow owns functional correctness: connectivity, nets, symbols, hierarchy, ERC, netlist, and PCB DRC.

Do not chase schematic visual score, text collisions, wire-corner count, or label/value tightening here unless the user specifically asks. Use `kicad-sch-cleanup-loop` for score-driven schematic cleanup.

## Report format

Keep the report short and specific:

```
ERC: pass (or: exit=N, same as before edit)
Netlist: intended changes only (or: identical / path-only diff)
Inspect: total=<score> collisions=<count>
PCB DRC: pass
```

On failure, include the failing stage, report path, and the first actionable issue:

```
FAIL: New ERC violation
  See tmp/erc.rpt
  First: ...
```
