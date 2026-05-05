---
name: kicad-sch-cleanup-loop
description: Use when the user asks to clean up, tidy, beautify, or improve the layout/readability/`sch inspect` score of a KiCad schematic sheet. Stage-2 visual pass; not for connectivity changes.
---

# KiCad Schematic Visual Cleanup Loop

Main loop plans + scores; subagents edit one cluster at a time. Edit conventions: `kicad-hardware-editing`. CLI: `kicad-tool`. Connectivity-parity: `kicad-validate`.

Inputs: `<sheet>` (child) and `<top>` (the project's top-level `.kicad_sch`).

## CLI-only

Never `Read`/`cat`/`grep` the `.kicad_sch` (thousands of S-expr lines blow context). Use `sch query` (`region`/`symbol`/`pin`/`wire`/`label`/`net`/`list`) and mutate via `sch edit`. Missing query/edit → extend `kicad-tool`.

## Baseline

```bash
kicad-tool sch validate <top> --sheet <sheet> \
  --save-baseline tmp/cleanup-baseline > tmp/cleanup-baseline.json
```

Repo-root-relative paths. No `2>&1` (stderr breaks JSON). Record `score.total`, `collisions`, `symbol_wire_conflicts`.

## Phase 0 — area plan (mandatory before any edit)

Greedy "fix highest-priority first" produces myopic rewires that later moves invalidate. Plan the target area with surrounding context first.

0. **Bird's-eye framing — three zoom levels.** One sentence each before clustering:
   - **Board**: does cleaning *this* sheet now fit the project's critical path, or should it wait?
   - **Sheet**: what function does the area serve, and will cleaning it leave the rest of the sheet stylistically aligned or force a follow-up pass?
   - **Area**: what does "done" look like at the bbox (uniform rows / mirror / min-collisions) — not per-issue.
   Then one non-cleanup alternative the request might really mean (shorten Value text, widen pitch, hide References, restructure rail, *not now*). Surface it before clustering if it could be the better answer — clustering can't recover from solving the wrong problem.
1. **Target bbox** = user instruction + latest validate JSON, expanded ~one local block.
2. **Render** to `tmp/cleanup-overview.png` via `sch render-region`. Read alongside JSON.
3. **Cluster issues** (≤6) by spatial locality + topological coupling (shared symbol/net/pin field).
4. **Order clusters** by:
   - **Anchors first** (MCU / multi-pin connector / MOSFET orientation) — late anchor moves waste earlier rewires.
   - **Topology before cosmetics** — `symbol_wire_conflicts`/`collisions` before `wire_corner_count`/`diagonal`/`wire_length`.
   - **Independence** — non-coupled clusters: pick whichever unblocks more.
   - **Defer label/value placement to Phase 2** (Phase 1 component moves invalidate it).
5. **Per-cluster**: name, bbox, issues, fix preference (rewire → rotate/flip label → rotate/flip small comp → move label → move comp → reshape), dependencies on earlier clusters.
   - **Wire relocate/delete tap audit (mandatory).** Enumerate every connection on the original wire — wire endpoints, junctions, pin endpoints, labels — via `sch query region` over the wire's bbox (+~0.01 mm). Endpoint stubs orphan mid-span taps. Pass tap list to subagent.
6. **Write `tmp/cleanup-plan.md`** — clusters in order, one block each. Update as work progresses.

## Phase 1 — execute, validate per cluster

Score is a **long-horizon** target: an anchor rotation may temporarily lengthen neighbor wires, paying off only after 1–2 more clusters. Use checkpoints, not per-edit gating, for score.

### Two gates per dispatch

- **Hard gate (immediate revert).** Baseline-diff exit must be 0 (no new ERC, no removed nets, no removed nodes). Connectivity damage → `git checkout -- <sheet>`, record `(cluster, approach)`. Never tolerate.
- **Soft gate (deferred).** Score regression alone does **not** revert. Enter exploratory state with a checkpoint cap.

### Checkpoint loop

**Last-good checkpoint** = most recent state with `score.total ≤` baseline checkpoint. Materialized by `tmp/cleanup-baseline/` (only refreshed on accept) + working tree. Rollback = `git checkout -- <sheet>` (no stash/branch). Accepting mid-streak resets the streak counter to 0 — there is no "streak with accepted edits in the middle".

For each cluster in planned order:

1. **Re-render cluster** to `tmp/cleanup-area.png`, re-query bbox issues. Skip if already resolved upstream.
2. **Dispatch one subagent** with: cluster name, bbox, `tmp/cleanup-area.png`, issue records, symbols/labels/wires inside, pre-edit `score.total`, planned approach + rationale, fix preference, and a note that short-term score regression is allowed if it sets up the next cluster (prefer non-regressing fixes when equivalent).
3. **Validate**: `sch validate <top> --sheet <sheet> --baseline tmp/cleanup-baseline > tmp/cleanup-latest.json`
   - **Hard-gate fail** → revert, record, next cluster.
   - **Hard-gate pass, score ≤ last-good** → accept as new last-good. Refresh baseline (`--save-baseline tmp/cleanup-baseline`). Clear streak.
   - **Hard-gate pass, score worse** → keep edits, mark exploratory, **do not refresh baseline**, increment streak.
4. **After every exploratory dispatch**: re-render `tmp/cleanup-overview.png`, read it, compare against the plan's predicted *intermediate* topology. Diverges → planning miss: roll streak back, record divergence, update `tmp/cleanup-plan.md`.
5. **Checkpoint cap.** Roll back the entire exploratory streak when any of: streak == 3 without recovering ≤ last-good, single-step jump >~20% above last-good (runaway), or overview diverges from plan.
6. **Re-plan** when topology shifts around downstream clusters.

Stop Phase 1 when:
- all clusters processed (end on last-good — if exploratory, roll back first),
- a cluster exhausts every approach without progress and plan has no fallback,
- two consecutive cap rollbacks (plan itself is wrong — stop and re-plan with user). Track the cap-rollback count in `tmp/cleanup-plan.md` alongside the cluster log.

## Phase 2 — label/value tightening

After Phase 1 stabilizes positions. Cosmetic only — long-horizon argument does **not** apply. Per-cluster strict gate: accept only if ERC parity, `score.total` not worse, no new collisions; else revert immediately. Group components into local clusters; subagent places `Value`/`Reference` adjacent to symbol body and tightens labels onto wire endpoints — no symbol moves unless trivial. Stop after one full pass with zero accepts.

### Placement geometry — render first, then solve

`wire_hits` is data, not a stop sign. Order of operations:

1. **Render and read the PNG.** `sch render-region` over the area, look at it. Identify visually: which side of the body is open, where rails / stubs / siblings sit. The PNG answers "where could the label go" before any math.
2. **Probe bbox dims — mandatory.** Place the property at any candidate, run `sch inspect`, read its actual `bbox.{x1,y1,x2,y2}`. Do **not** estimate from `n_chars × 1.1 mm`; the probe is cheap and the estimate misses justify/baseline offsets. The probe edit is also the first edit of the cluster — keep it.
3. **Use the probe to derive the bbox→anchor transform.** From the probed `bbox` and the input `at = (ax, ay)`: `dx_left = ax - bbox.x1`, `dx_right = bbox.x2 - ax`, `dy_top = ay - bbox.y1`, `dy_bot = bbox.y2 - ay`. KiCad anchor is **not** bbox-center — it's text justify + font baseline, so these four offsets are asymmetric. Reuse them for the final position.
4. **Decompose per axis.** Real hit ⇔ bbox x-range AND y-range both overlap obstacle. Same y, different x = harmless.
5. **Rotated 2-pin passives:** stub runs at `body_center_x`. Anchor Value/Reference at `body_left_x` or `body_right_x` — never center — so the narrow vertical-text bbox is disjoint from the stub and y can pass through the rail-to-pin band even when shorter than text height. Note: the property's own `angle` field combines with the parent symbol's rotation; vertical-rendered text often appears with property `angle=0` when the symbol itself is rotated 90°/270°. Trust the probed `bbox`, not the angle field.
6. **Tightness by arithmetic, using probed offsets.** Place above body top: `anchor_y = body_top_y - dy_bot - 0.25`. Below: `anchor_y = body_bottom_y + dy_top + 0.25`. Left: `anchor_x = obstacle_x - dx_right - 0.25`. Right: `anchor_x = obstacle_x + dx_left + 0.25`. ε = 0.25 mm clearance. **Source for `body_top_y` etc.**: use `sch query region <bbox covering the symbol>`, then read `symbols[<i>].bbox` — that is the graphic body only. Do **not** use `sch inspect`'s `component_hits[].bbox` (can include attached property text), and `sch query symbol` does **not** return a bbox.
7. **Unified siblings with different widths:** keep the same `(x, y)` formula; anchor consistency beats visual symmetry.

Forbidden: "try A, try B, try C". The probe edit is your one allowed exploratory placement; after that, re-read the PNG, apply the offset arithmetic, commit.

## Report

```
Sheet: <sheet>
Baseline: total=<n0> ERC=<e0>
Plan: <k> clusters
Phase 1: total=<n1> Δ=<n1-n0> checkpoints=<a1> hard-reverts=<r1> exploratory-rollbacks=<x1> skipped=<s1>
Phase 2: accepted=<a2> passes=<p2>
Final ERC: <parity | listed intentional>
```

Show `git diff --stat <sheet>`. Do not commit unless asked.

## Hard rules

- No edit before `tmp/cleanup-plan.md` exists.
- One dispatch = one cluster = one validate. Subagents never validate; main loop always does. Subagents always get the rendered PNG path, not just bbox JSON.
- **Connectivity regression ⇒ immediate revert.** Never tolerated.
- **Score regression ≠ immediate revert.** Exploratory; render overview; judge vs plan. Roll streak back only on (a) streak == 3 no recovery, (b) >~20% jump, or (c) overview divergence. Final state must be last-good.
- **Wire relocate ⇒ enumerate every tap**, not just endpoints. ERC count parity misses orphaned taps; baseline-diff `removed_nodes` catches it.
- Reuse stable `tmp/` filenames: `cleanup-baseline.json`, `cleanup-latest.json`, `cleanup-overview.png`, `cleanup-area.png`, `cleanup-plan.md`.
