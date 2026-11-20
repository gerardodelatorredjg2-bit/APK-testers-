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


_VORBIS_IDENT = b"\\x01vorbis"
_VORBIS_COMMENT = b"\\x03vorbis"
_VORBIS_TAG_NAME = re.compile(rb"^[A-Za-z0-9_]+$")


def _ogg_crc32(data: bytes | bytearray) -> int:
    """Calculate the non-reflected Ogg page CRC (polynomial 0x04C11DB7)."""
    crc = 0
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    return crc


def _update_ogg_page_crc(page: bytearray) -> None:
    page[22:26] = b"\\x00\\x00\\x00\\x00"
    page[22:26] = struct.pack("<I", _ogg_crc32(page))


def _valid_vorbis_comment_packet(packet: bytes) -> bool:
    if not packet.startswith(_VORBIS_COMMENT) or len(packet) < 16:
        return False
    position = 7
    if position + 4 > len(packet):
        return False
    vendor_length = struct.unpack_from("<I", packet, position)[0]
    position += 4
    if position + vendor_length + 4 > len(packet):
        return False
    vendor = packet[position:position + vendor_length]
    if b"\\x00" in vendor:
        return False
    try:
        vendor.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return False
    position += vendor_length
    count = struct.unpack_from("<I", packet, position)[0]
    position += 4
    if count > 100000:
        return False
    for _ in range(count):
        if position + 4 > len(packet):
            return False
        comment_length = struct.unpack_from("<I", packet, position)[0]
        position += 4
        if position + comment_length > len(packet):
            return False
        comment = packet[position:position + comment_length]
        position += comment_length
        if b"\\x00" in comment or b"=" not in comment:
            return False
        name, value = comment.split(b"=", 1)
        if not _VORBIS_TAG_NAME.fullmatch(name):
            return False
        try:
            value.decode("utf-8", "strict")
        except UnicodeDecodeError:
            return False
    return position + 1 == len(packet) and packet[position] == 1


def _clean_vorbis_comment_packet(old_packet: bytes) -> bytes:
    # Preserve the exact packet size. Only the comment header is changed;
    # the Ogg page layout and every compressed audio packet remain byte-identical.
    size = len(old_packet)
    if size < 16:
        raise RuntimeError(f"Vorbis comment packet is too short: {size}")
    vendor_length = size - 16
    vendor = b"Machine Party metadata cleaned"
    if vendor_length < len(vendor):
        vendor = b" " * vendor_length
    else:
        vendor += b" " * (vendor_length - len(vendor))
    cleaned = (
        _VORBIS_COMMENT
        + struct.pack("<I", len(vendor))
        + vendor
        + struct.pack("<I", 0)
        + b"\\x01"
    )
    if len(cleaned) != size:
        raise AssertionError("Vorbis comment packet size changed")
    return cleaned


def _sanitize_ogg_comment_bytes(data: bytes) -> tuple[bytes, bool]:
    pages: list[bytearray] = []
    states: dict[int, dict] = {}
    offset = 0
    while offset < len(data):
        if offset + 27 > len(data) or data[offset:offset + 4] != b"OggS":
            raise RuntimeError(f"Invalid Ogg page capture at offset {offset}")
        segment_count = data[offset + 26]
        header_end = offset + 27 + segment_count
        if header_end > len(data):
            raise RuntimeError("Truncated Ogg segment table")
        lacing = list(data[offset + 27:header_end])
        body_length = sum(lacing)
        page_end = header_end + body_length
        if page_end > len(data):
            raise RuntimeError("Truncated Ogg page body")

        page_index = len(pages)
        page = bytearray(data[offset:page_end])
        pages.append(page)
        serial = struct.unpack_from("<I", page, 14)[0]
        state = states.setdefault(serial, {
            "packet_index": 0,
            "packet": bytearray(),
            "fragments": [],
            "is_vorbis": False,
            "changed_pages": set(),
            "changed": False,
        })
        body_position = header_end - offset
        for segment_length in lacing:
            fragment_start = body_position
            fragment = page[fragment_start:fragment_start + segment_length]
            state["packet"].extend(fragment)
            state["fragments"].append((page_index, fragment_start, segment_length))
            body_position += segment_length
            if segment_length < 255:
                packet = bytes(state["packet"])
                packet_index = state["packet_index"]
                if packet_index == 0:
                    state["is_vorbis"] = packet.startswith(_VORBIS_IDENT)
                elif packet_index == 1 and state["is_vorbis"] and packet.startswith(_VORBIS_COMMENT):
                    if not _valid_vorbis_comment_packet(packet):
                        cleaned = _clean_vorbis_comment_packet(packet)
                        cursor = 0
                        for affected_page, fragment_start, fragment_length in state["fragments"]:
                            if fragment_length:
                                pages[affected_page][fragment_start:fragment_start + fragment_length] = (
                                    cleaned[cursor:cursor + fragment_length]
                                )
                            cursor += fragment_length
                        if cursor != len(cleaned):
                            raise AssertionError("Ogg packet fragment map mismatch")
                        state["changed_pages"].update(fragment[0] for fragment in state["fragments"])
                        state["changed"] = True
                state["packet_index"] += 1
                state["packet"].clear()
                state["fragments"].clear()
        offset = page_end

    changed_pages: set[int] = set()
    changed = False
    for state in states.values():
        if state["changed"]:
            changed = True
            changed_pages.update(state["changed_pages"])
    if not changed:
        return data, False

    output = bytearray()
    for index, page in enumerate(pages):
        if index in changed_pages:
            _update_ogg_page_crc(page)
        output.extend(page)
    return bytes(output), True


def sanitize_vorbis_comments(project: Path) -> tuple[int, int]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg and ffprobe are required to verify OGG audio")
    processed = 0
    skipped = 0
    for path in sorted(project.rglob("*.ogg")):
        codec = audio_codec(ffprobe, path)
        if codec != "vorbis":
            skipped += 1
            continue
        before_hash = decoded_audio_hash(ffmpeg, path)
        original_bytes = path.read_bytes()
        cleaned_bytes, changed = _sanitize_ogg_comment_bytes(original_bytes)
        if not changed:
            skipped += 1
            continue
        temp_path = path.with_name(path.name + ".metadata-clean")
        try:
            temp_path.write_bytes(cleaned_bytes)
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
