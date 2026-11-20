#!/usr/bin/env python3
"""Focused source audit for recovered Machine Party minigames.

This runs inside Android CI, where the full recovered project is available.
It deliberately prints concise file/line evidence so gameplay defects can be
located without downloading the multi-gigabyte recovery artifact.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: audit_recovered_minigames.py PROJECT_DIR")

root = Path(sys.argv[1]).resolve()
mini = root / "minigames"
if not (root / "project.godot").is_file() or not mini.is_dir():
    raise SystemExit(f"Not a recovered Godot project: {root}")

print("=== MINIGAME DIRECTORY INVENTORY ===")
for folder in sorted(p for p in mini.iterdir() if p.is_dir()):
    scenes = sorted(folder.rglob("*.tscn"))
    scripts = sorted(folder.rglob("*.gd"))
    print(f"GAME {folder.name}: scenes={len(scenes)} scripts={len(scripts)}")
    for scene in scenes[:8]:
        print(f"  SCENE {scene.relative_to(root)}")

patterns = {
    "TRANSITION": re.compile(r"change_scene|fade_overlay|fade_finished|GlobalOverlay|transition|intermission|queue_free", re.I),
    "BLACK_SCREEN": re.compile(r"ColorRect|color\s*=\s*Color\.BLACK|modulate|visible\s*=\s*false|show\(|hide\(|canvas_layer|z_index", re.I),
    "SCULPTURE_SHAPE": re.compile(r"shape_cutter|PatternMatcher|PatternPoints|cut_texture|image_texture|ImageTexture|create_from_image|shader|albedo_texture", re.I),
    "MINES_EXPLOSION": re.compile(r"mine|bomb|explod|detonat|flash|damage|death|hit", re.I),
    "CRAWL_ANIMATION": re.compile(r"crawl|crawling|drag|prone|animationplayer|animation_tree|spriteframes|\.play\(", re.I),
}
global_pattern = re.compile("|".join(f"(?:{p.pattern})" for p in patterns.values()), re.I)

print("=== RELEVANT SCRIPT EVIDENCE ===")
script_count = 0
for path in sorted(mini.rglob("*.gd")):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        continue
    matches = [(n, line.strip()) for n, line in enumerate(lines, 1) if global_pattern.search(line)]
    if not matches:
        continue
    script_count += 1
    print(f"SCRIPT {path.relative_to(root)} ({len(matches)} matching lines)")
    emitted = 0
    for number, line in matches:
        tags = [name for name, pattern in patterns.items() if pattern.search(line)]
        print(f"  L{number} [{','.join(tags)}] {line[:220]}")
        emitted += 1
        if emitted >= 14:
            print("  ...")
            break
print(f"RELEVANT_SCRIPT_COUNT={script_count}")

print("=== SHAPE CUTTER SOURCE ===")
shape = mini / "shape_cutter"
if shape.is_dir():
    for path in sorted(shape.rglob("*.gd")):
        if path.name in {"shape_cutter_test.gd", "shape_cutter.gd", "pattern_matcher.gd"} or "shape_cutter" in path.name:
            print(f"--- {path.relative_to(root)} ---")
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            for number, line in enumerate(lines[:220], 1):
                print(f"{number:04d}: {line[:220]}")

print("=== ANIMATION ASSET REFERENCES ===")
for path in sorted(mini.rglob("*.tscn")):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        continue
    matches = [(n, line.strip()) for n, line in enumerate(lines, 1)
               if re.search(r"AnimationPlayer|AnimationLibrary|SpriteFrames|animation\s*=|animations\s*=", line, re.I)]
    if matches:
        print(f"ANIMATION_SCENE {path.relative_to(root)}")
        for n, line in matches[:12]:
            print(f"  L{n} {line[:200]}")


# Export only editable gameplay source files; omit the large media payload.
source_roots = ("minigames", "autoloads", "modules", "scenes", "addons")
source_extensions = {".gd", ".tscn", ".tres", ".gdshader", ".shader", ".import", ".cfg", ".gdextension", ".cs"}
archive_path = root.parent / "recovered-gameplay-source.zip"
written = 0
with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as bundle:
    for folder_name in source_roots:
        folder = root / folder_name
        if not folder.is_dir():
            continue
        for source_path in sorted(folder.rglob("*")):
            if not source_path.is_file() or source_path.suffix.lower() not in source_extensions:
                continue
            if ".godot" in source_path.parts:
                continue
            bundle.write(source_path, source_path.relative_to(root).as_posix())
            written += 1
    for root_file in ("project.godot", "bus_layout.tres", "default_bus_layout.tres", "export_presets.cfg"):
        source_path = root / root_file
        if source_path.is_file():
            bundle.write(source_path, source_path.relative_to(root).as_posix())
            written += 1
print(f"GAMEPLAY_SOURCE_BUNDLE={archive_path} FILES={written} BYTES={archive_path.stat().st_size}")
print("AUDIT_RECOVERED_MINIGAMES_COMPLETE")
