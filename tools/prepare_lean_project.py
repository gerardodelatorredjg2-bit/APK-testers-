#!/usr/bin/env python3
"""Prepare a disposable recovered Machine Party copy for a clean Android build.

Only removes optional desktop/Steam native extension declarations and replaces
the unavailable VOIP AudioEffectGate with the built-in audio capture path.
The original recovered archive must remain untouched.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import re

OPTIONAL_EXTENSIONS = (
    "addons/audio_gate/audio_gate.gdextension",
    "addons/godotsteam/godotsteam.gdextension",
    "addons/steam-multiplayer-peer/steam-multiplayer-peer.gdextension",
)
MISSING_SCENE_UID = "uid://bwixccr50fbc2"
MISSING_SCENE_PATH = "res://modules/user_interface/intermission_control_input_label/intermission_control_input_label.tscn"

def prepare_bus_layout(path: Path) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    before = text

    # Resolve Gate subresource IDs first, then remove only the exact bus/effect
    # slots that reference those IDs. Never remove enabled flags for Capture,
    # filters, or any unrelated built-in effect.
    gate_ids = re.findall(
        r'(?m)^\\[sub_resource type="AudioEffectGate" id="([^"]+)"\\]$',
        text,
    )
    for gate_id in gate_ids:
        slots = re.findall(
            rf'(?m)^bus/(\\d+)/effect/(\\d+)/effect = SubResource\\("{re.escape(gate_id)}"\\)$',
            text,
        )
        for bus_index, effect_index in slots:
            text = re.sub(
                rf'(?m)^bus/{bus_index}/effect/{effect_index}/effect = SubResource\\("{re.escape(gate_id)}"\\)\\n?',
                "",
                text,
            )
            text = re.sub(
                rf'(?m)^bus/{bus_index}/effect/{effect_index}/enabled = (?:true|false)\\n?',
                "",
                text,
            )

        # Remove the complete section for this unavailable custom effect.
        text = re.sub(
            rf'(?ms)^\\[sub_resource type="AudioEffectGate" id="{re.escape(gate_id)}"\\]\\n.*?(?=^\\[|\\Z)',
            "",
            text,
            count=1,
        )

    # Keep the resource header's load_steps consistent with all remaining
    # subresources (the resource itself contributes one step).
    subresources = len(re.findall(r'(?m)^\\[sub_resource ', text))
    text = re.sub(
        r'(?m)^(\\[gd_resource[^\\n]*load_steps=)\\d+',
        lambda m: m.group(1) + str(subresources + 1),
        text,
        count=1,
    )

    if text != before:
        path.write_text(text, encoding="utf-8")
        return True
    return False

def prepare_project(project: Path, compatibility_renderer: bool = True) -> dict:
    project = project.resolve()
    if not (project / "project.godot").is_file():
        raise ValueError(f"project.godot missing: {project}")

    removed = []
    for rel in OPTIONAL_EXTENSIONS:
        target = project / rel
        if target.exists():
            target.unlink()
            removed.append(rel)

    bus_layouts = []
    for name in ("bus_layout.tres", "default_bus_layout.tres"):
        if prepare_bus_layout(project / name):
            bus_layouts.append(name)

    uid_fixed = []
    for path in project.rglob("*.gd"):
        if ".godot" in path.relative_to(project).parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if MISSING_SCENE_UID in text:
            path.write_text(text.replace(MISSING_SCENE_UID, MISSING_SCENE_PATH), encoding="utf-8")
            uid_fixed.append(path.relative_to(project).as_posix())

    renderer_changed = False
    if compatibility_renderer:
        cfg = project / "project.godot"
        text = cfg.read_text(encoding="utf-8")
        # Android's Compatibility renderer targets OpenGL ES 3 and is the
        # widest fit for older Android GPUs; desktop keeps the base renderer.
        if re.search(r'(?m)^renderer/rendering_method\.mobile=', text):
            new = re.sub(r'(?m)^renderer/rendering_method\.mobile=.*$', 'renderer/rendering_method.mobile="gl_compatibility"', text)
        else:
            marker = "[rendering]"
            if marker not in text:
                text += "\n[rendering]\n"
            new = text.replace(marker, marker + '\nrenderer/rendering_method.mobile="gl_compatibility"', 1)
        if new != text:
            cfg.write_text(new, encoding="utf-8")
            renderer_changed = True

    return {
        "removed_optional_extensions": removed,
        "bus_layouts_without_native_gate": bus_layouts,
        "missing_scene_uid_references_fixed": uid_fixed,
        "android_compatibility_renderer_set": renderer_changed,
    }

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--keep-base-renderer", action="store_true")
    args = ap.parse_args()
    report = prepare_project(args.project, compatibility_renderer=not args.keep_base_renderer)
    for key, value in report.items():
        print(f"{key}: {value}")

if __name__ == "__main__":
    main()
