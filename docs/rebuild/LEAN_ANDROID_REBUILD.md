# Machine Party — Lean Android reconstruction

## Goal
Rebuild the same Machine Party game from the recovered Godot source on a clean branch, not layer more patches onto the old Android export. Preserve original gameplay, minigames, characters, scenes, and LAN play while reducing Android package size and runtime resource load.

## Non-negotiables
- Preserve original gameplay/minigames and visual identity; no placeholder game substitution.
- Keep the recovered source and existing branches untouched.
- Android-first: mobile renderer, conservative effects, scalable resolution, touch controls.
- LAN host/join and automatic local discovery remain part of the target.
- Stop micro-optimizing the old APK; changes should support the clean rebuild.
- Target APK 100–200 MB is a stretch target, not a guarantee. Determine feasibility from asset inventory and test exports.

## Phases
1. Recover & inventory: retrieve complete Godot project from existing recovery artifact; validate project, scripts, scenes, assets. Report file types/sizes, duplicates, largest resources and references.
2. Baseline playable: import in pinned Godot version, run desktop, validate menus and scene transitions, record errors.
3. Asset slimming: preserve originals; optimize textures/audio/meshes only after reference and visual checks; retain paths when possible.
4. Android runtime: mobile renderer, low/medium/high presets, reduced overdraw/effects, adaptive resolution, memory-conscious resource loading and touch UI.
5. Gameplay/LAN recovery: validate minigames, touch editor/hide controls, HOST/JOIN, discovery and network sync.
6. QA & packaging: smoke tests, export/install checks ARMv7/ARM64, size report; publish only after checks.

## Starting point
Recovered source exists in workflow artifact from run 44350091150, named machine-party-recovery. The repo contains patch/build scripts, not the complete Godot asset tree. This rebuild branch stays separate from old APK patching.
