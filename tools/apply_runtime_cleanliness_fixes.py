#!/usr/bin/env python3
"""Apply mobile runtime/log cleanliness fixes to a recovered Machine Party project.

The OGG pass remuxes Vorbis streams without transcoding, strips malformed tags,
and verifies decoded PCM hashes are unchanged before replacing any original.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

MENU_RELATIVE = Path("scripts/components/menu visuals/menu_visual_manager.gd")


def patch_menu_visual_manager(project: Path) -> bool:
    path = project / MENU_RELATIVE
    if not path.is_file():
        raise RuntimeError(f"Required menu visual manager not found: {path}")
    source = path.read_text(encoding="utf-8")
    old_required = "\t\tmargin_container.size = size_required"
    old_default = "\t\tmargin_container.size = size_default"
    new_required = "\t\t_apply_margin_container_rect_size(size_required)"
    new_default = "\t\t_apply_margin_container_rect_size(size_default)"
    changed = False
    if old_required in source:
        source = source.replace(old_required, new_required, 1)
        changed = True
    if old_default in source:
        source = source.replace(old_default, new_default, 1)
        changed = True
    helper = (
        "func _apply_margin_container_rect_size(target_size: Vector2) -> void:\n"
        "\tif margin_container == null:\n"
        "\t\treturn\n"
        "\t# This Control is anchored to all four parent edges. Set its offsets,\n"
        "\t# not size, so the requested dimensions are stable without anchor warnings.\n"
        "\tvar parent_size: Vector2 = margin_container.get_parent_area_size()\n"
        "\tmargin_container.offset_right = margin_container.offset_left + target_size.x - parent_size.x\n"
        "\tmargin_container.offset_bottom = margin_container.offset_top + target_size.y - parent_size.y\n\n"
    )
    if "func _apply_margin_container_rect_size(" not in source:
        marker = "func show_post_original():"
        if marker not in source:
            raise RuntimeError("Could not locate show_post_original() insertion point")
        source = source.replace(marker, helper + marker, 1)
        changed = True
    if "margin_container.size = size_required" in source or "margin_container.size = size_default" in source:
        raise RuntimeError("Direct MarginContainer.size assignments remain after patch")
    if changed:
        path.write_text(source, encoding="utf-8")
    return changed


def disable_unsupported_ssao(project: Path) -> int:
    count = 0
    pattern = re.compile(r"(?m)^(\s*ssao_enabled\s*=\s*)true(\s*(?:#.*)?)$")
    for suffix in ("*.tscn", "*.tres"):
        for path in project.rglob(suffix):
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            updated, replacements = pattern.subn(r"\1false\2", source)
            if replacements:
                path.write_text(updated, encoding="utf-8")
                count += replacements
    return count


def run_checked(args: list[str]) -> str:
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(args)}\n{detail}")
    return result.stdout.strip()


def decoded_audio_hash(ffmpeg: str, path: Path) -> str:
    output = run_checked([
        ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-i", str(path),
        "-map", "0:a:0", "-f", "hash", "-hash", "sha256", "-",
    ])
    match = re.search(r"SHA256=([0-9a-fA-F]{64})", output)
    if not match:
        raise RuntimeError(f"Could not obtain decoded PCM hash for {path}: {output!r}")
    return match.group(1).lower()


def audio_codec(ffprobe: str, path: Path) -> str:
    output = run_checked([
        ffprobe, "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name", "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    if not output:
        raise RuntimeError(f"No audio stream found in OGG file: {path}")
    return output.splitlines()[0].strip()


def sanitize_vorbis_comments(project: Path) -> tuple[int, int]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    vorbiscomment = shutil.which("vorbiscomment")
    if not ffmpeg or not ffprobe or not vorbiscomment:
        raise RuntimeError("ffmpeg, ffprobe and vorbiscomment are required for lossless OGG metadata cleanup")
    processed = 0
    skipped = 0
    for path in sorted(project.rglob("*.ogg")):
        codec = audio_codec(ffprobe, path)
        if codec != "vorbis":
            skipped += 1
            continue
        before_hash = decoded_audio_hash(ffmpeg, path)
        temp_path = path.with_name(path.stem + ".metadata-clean.ogg")
        try:
            # vorbiscomment rewrites only the Vorbis comment header and copies
            # the compressed audio packets; no decode/re-encode is performed.
            # /dev/null as the comment file intentionally removes malformed tags.
            run_checked([
                vorbiscomment, "-q", "-w", "-R", "-c", os.devnull,
                str(path), str(temp_path),
            ])
            after_codec = audio_codec(ffprobe, temp_path)
            if after_codec != "vorbis":
                raise RuntimeError(f"Codec changed while cleaning {path}: {after_codec}")
            after_hash = decoded_audio_hash(ffmpeg, temp_path)
            if before_hash != after_hash:
                raise RuntimeError(f"Decoded audio changed while cleaning comments: {path}")
            os.replace(temp_path, path)
            processed += 1
        finally:
            if temp_path.exists():
                temp_path.unlink()
    return processed, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    if not (project / "project.godot").is_file():
        parser.error(f"Not a Godot project: {project}")
    menu_changed = patch_menu_visual_manager(project)
    ssao_count = disable_unsupported_ssao(project)
    vorbis_count, skipped_count = sanitize_vorbis_comments(project)
    print(f"MENU_ANCHOR_FIX={'applied' if menu_changed else 'already_applied'}")
    print(f"MOBILE_SSAO_DISABLED={ssao_count}")
    print(f"VORBIS_METADATA_SANITIZED={vorbis_count}")
    print(f"NON_VORBIS_OGG_SKIPPED={skipped_count}")
    print("RUNTIME_CLEANLINESS_FIXES_PASS: menu geometry, mobile renderer settings, and lossless audio metadata checks")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"RUNTIME_CLEANLINESS_FIXES_FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
