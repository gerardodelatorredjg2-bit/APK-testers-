#!/usr/bin/env python3
"""Repair malformed Vorbis comment metadata without re-encoding audio.

Audio packets are compared before and after remuxing. If FFmpeg changes any
compressed audio packet, the original file is kept and the command fails.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path


def ogg_packets(data: bytes) -> list[bytes]:
    packets: list[bytes] = []
    packet = bytearray()
    pos = 0

    while pos < len(data):
        if pos + 27 > len(data) or data[pos:pos + 4] != b"OggS":
            raise ValueError(f"invalid Ogg page at byte {pos}")

        segment_count = data[pos + 26]
        segment_start = pos + 27
        body_start = segment_start + segment_count
        if body_start > len(data):
            raise ValueError(f"truncated Ogg segment table at byte {pos}")

        lacing = data[segment_start:body_start]
        body_length = sum(lacing)
        body_end = body_start + body_length
        if body_end > len(data):
            raise ValueError(f"truncated Ogg page body at byte {pos}")

        body = data[body_start:body_end]
        body_offset = 0
        for segment_length in lacing:
            packet.extend(body[body_offset:body_offset + segment_length])
            body_offset += segment_length
            if segment_length < 255:
                packets.append(bytes(packet))
                packet.clear()

        pos = body_end

    if packet:
        raise ValueError("Ogg stream ends with an incomplete packet")
    return packets


def vorbis_comment_packet(packets: list[bytes]) -> bytes | None:
    return next((p for p in packets if p.startswith(b"\x03vorbis")), None)


def invalid_comment_reason(packet: bytes | None) -> str | None:
    if packet is None:
        return None

    try:
        offset = 7
        vendor_length = int.from_bytes(packet[offset:offset + 4], "little")
        offset += 4
        vendor = packet[offset:offset + vendor_length]
        offset += vendor_length
        vendor.decode("utf-8")
        if offset + 4 > len(packet):
            return "truncated comment count"

        count = int.from_bytes(packet[offset:offset + 4], "little")
        offset += 4

        for index in range(count):
            if offset + 4 > len(packet):
                return f"truncated comment length at index {index}"
            length = int.from_bytes(packet[offset:offset + 4], "little")
            offset += 4
            if offset + length > len(packet):
                return f"truncated comment value at index {index}"
            value = packet[offset:offset + length]
            offset += length

            if b"=" not in value:
                return f"comment {index} has no NAME=VALUE separator"
            key, _ = value.split(b"=", 1)
            if not key or any(byte < 0x20 or byte > 0x7D or byte == 0x3D for byte in key):
                return f"comment {index} has an invalid field name"
            value.decode("utf-8")

        if offset >= len(packet) or packet[offset] != 1:
            return "missing or invalid Vorbis framing bit"
    except (UnicodeDecodeError, IndexError, ValueError) as exc:
        return f"invalid Vorbis comment packet: {exc}"

    return None


def audio_packet_digest(packets: list[bytes]) -> str:
    # A Vorbis stream starts with identification, comment and setup packets.
    if len(packets) < 3:
        raise ValueError("Vorbis stream has fewer than three header packets")
    digest = hashlib.sha256()
    for packet in packets[3:]:
        digest.update(len(packet).to_bytes(8, "little"))
        digest.update(packet)
    return digest.hexdigest()


def repair(path: Path, ffmpeg: str) -> str | None:
    packets = ogg_packets(path.read_bytes())
    comment_packet = vorbis_comment_packet(packets)
    if comment_packet is None:
        return None

    reason = invalid_comment_reason(comment_packet)
    if reason is None:
        return None

    before_digest = audio_packet_digest(packets)
    temp = path.with_name(path.name + ".metadata-repair.tmp.ogg")
    if temp.exists():
        temp.unlink()

    try:
        command = [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(path), "-map_metadata", "-1", "-c:a", "copy", "-f", "ogg",
            str(temp),
        ]
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if not temp.is_file() or temp.stat().st_size == 0:
            raise RuntimeError("FFmpeg produced an empty output")

        repaired_packets = ogg_packets(temp.read_bytes())
        repaired_comment = vorbis_comment_packet(repaired_packets)
        remaining_problem = invalid_comment_reason(repaired_comment)
        if remaining_problem:
            raise RuntimeError(f"comment remains invalid after remux: {remaining_problem}")

        after_digest = audio_packet_digest(repaired_packets)
        if before_digest != after_digest:
            raise RuntimeError("compressed audio packets changed; refusing replacement")

        shutil.copystat(path, temp)
        os.replace(temp, path)
        return reason
    finally:
        if temp.exists():
            temp.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove invalid Vorbis comment metadata while preserving encoded audio packets."
    )
    parser.add_argument("project", type=Path, help="Godot project directory")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg executable")
    args = parser.parse_args()

    if not args.project.is_dir():
        parser.error(f"project directory does not exist: {args.project}")
    if shutil.which(args.ffmpeg) is None:
        print(f"ERROR: FFmpeg executable not found: {args.ffmpeg}", file=sys.stderr)
        return 2

    files = sorted(p for p in args.project.rglob("*.ogg") if p.is_file())
    repaired = 0
    checked = 0
    failures: list[str] = []

    for path in files:
        try:
            reason = repair(path, args.ffmpeg)
            checked += 1
            if reason:
                repaired += 1
                print(f"REPAIRED: {path.relative_to(args.project)} ({reason})")
        except Exception as exc:
            failures.append(f"{path}: {exc}")

    print(f"Vorbis audit: checked={checked}, repaired={repaired}, failed={len(failures)}")
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
