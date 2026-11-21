#!/usr/bin/env python3
"""Shrink OGG assets and repair malformed Vorbis comments on a disposable copy.

The original recovery archive is never modified. Audio packet streams are
remuxed (not re-encoded) to remove malformed comments; lossy Vorbis Q4 is only
accepted when its output is smaller than the cleaned/current file.
"""
from __future__ import annotations
import argparse
import csv
import os
import shutil
import struct
import subprocess
from pathlib import Path

def has_invalid_vorbis_comments(path: Path) -> bool:
    """Detect a Vorbis comment entry without the required KEY=VALUE format."""
    packets: list[bytes] = []
    current = bytearray()
    try:
        with path.open("rb") as stream:
            while len(packets) < 2:
                header = stream.read(27)
                if len(header) != 27 or header[:4] != b"OggS" or header[4] != 0:
                    return False
                segment_count = header[26]
                lacing = stream.read(segment_count)
                if len(lacing) != segment_count:
                    return False
                body_size = sum(lacing)
                body = stream.read(body_size)
                if len(body) != body_size:
                    return False
                offset = 0
                for segment_size in lacing:
                    current.extend(body[offset:offset + segment_size])
                    offset += segment_size
                    if segment_size < 255:
                        packets.append(bytes(current))
                        current.clear()
                        if len(packets) == 2:
                            break
    except OSError:
        return False

    if len(packets) < 2 or not packets[1].startswith(b"\x03vorbis"):
        return False
    comment_packet = packets[1]
    try:
        vendor_length = struct.unpack_from("<I", comment_packet, 7)[0]
        offset = 11 + vendor_length
        comment_count = struct.unpack_from("<I", comment_packet, offset)[0]
        offset += 4
        if comment_count > 100000:
            return False
        for _ in range(comment_count):
            comment_length = struct.unpack_from("<I", comment_packet, offset)[0]
            offset += 4
            comment = comment_packet[offset:offset + comment_length]
            if len(comment) != comment_length:
                return False
            offset += comment_length
            if b"=" not in comment:
                return True
    except (IndexError, struct.error):
        return False
    return False

def run_ffmpeg(ffmpeg: str, args: list[str]) -> tuple[bool, str]:
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    detail = (result.stderr or "").strip().replace("\n", " ")[:300]
    return result.returncode == 0, detail

def optimize_audio(project: Path, quality: float = 4.0, min_bytes: int = 262144) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg was not found; install it before audio optimization.")
    project = project.resolve()
    if not (project / "project.godot").is_file():
        raise ValueError(f"Not a Godot project: {project}")

    rows = []
    before_total = after_total = saved_total = 0
    repaired_comments = 0
    encode_savings = 0

    for source in sorted(project.rglob("*.ogg")):
        if ".godot" in source.relative_to(project).parts:
            continue
        original_bytes = source.stat().st_size
        before_total += original_bytes
        current_bytes = original_bytes
        comment_status = "valid"
        encode_status = "skipped_small"
        temp_remux = source.with_name(source.name + ".lean-tags-tmp.ogg")
        temp_encode = source.with_name(source.name + ".lean-q4-tmp.ogg")

        try:
            if has_invalid_vorbis_comments(source):
                remux_ok, detail = run_ffmpeg(ffmpeg, [
                    "-i", str(source), "-map", "0:a:0", "-map_metadata", "-1",
                    "-vn", "-c:a", "copy", "-f", "ogg", str(temp_remux),
                ])
                if remux_ok and temp_remux.is_file() and temp_remux.stat().st_size > 0:
                    os.replace(temp_remux, source)
                    current_bytes = source.stat().st_size
                    repaired_comments += 1
                    comment_status = "repaired_no_audio_reencode"
                else:
                    if temp_remux.exists():
                        temp_remux.unlink()
                    comment_status = "repair_failed:" + detail

            if current_bytes >= min_bytes:
                encode_ok, detail = run_ffmpeg(ffmpeg, [
                    "-i", str(source), "-map", "0:a:0", "-map_metadata", "-1",
                    "-vn", "-c:a", "libvorbis", "-q:a", str(quality),
                    str(temp_encode),
                ])
                if not encode_ok or not temp_encode.is_file() or temp_encode.stat().st_size <= 0:
                    if temp_encode.exists():
                        temp_encode.unlink()
                    encode_status = "kept_after_encode_failure:" + detail
                else:
                    encoded_bytes = temp_encode.stat().st_size
                    if encoded_bytes < current_bytes:
                        saved = current_bytes - encoded_bytes
                        os.replace(temp_encode, source)
                        current_bytes = encoded_bytes
                        saved_total += saved
                        encode_savings += saved
                        encode_status = "reencoded_smaller"
                    else:
                        temp_encode.unlink()
                        encode_status = "kept_current_not_smaller"
        except Exception:
            if temp_remux.exists():
                temp_remux.unlink()
            if temp_encode.exists():
                temp_encode.unlink()
            raise

        after_total += current_bytes
        rows.append((
            source.relative_to(project).as_posix(),
            original_bytes,
            current_bytes,
            original_bytes - current_bytes,
            comment_status,
            encode_status,
            quality,
        ))

    report = project / "lean_audio_optimization.csv"
    with report.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow([
            "path", "input_bytes", "output_bytes", "saved_bytes",
            "vorbis_comments", "encoding_decision", "vorbis_quality",
        ])
        writer.writerows(rows)

    net_saved = before_total - after_total
    summary = {
        "tracks": len(rows),
        "input_bytes": before_total,
        "output_bytes": after_total,
        "saved_bytes": net_saved,
        "saved_percent": round(net_saved * 100.0 / before_total, 2) if before_total else 0.0,
        "malformed_comment_tracks_repaired": repaired_comments,
        "q4_encode_savings_bytes": encode_savings,
        "report": str(report),
    }
    print(
        "OGG optimization: {tracks} tracks, {before:.1f} MiB -> {after:.1f} MiB, "
        "net saved {saved:.1f} MiB ({percent:.2f}%).".format(
            tracks=len(rows),
            before=before_total / 1048576,
            after=after_total / 1048576,
            saved=net_saved / 1048576,
            percent=summary["saved_percent"],
        )
    )
    print(f"Malformed Vorbis comment tracks repaired without audio re-encoding: {repaired_comments}")
    print(f"Q4 audio savings: {encode_savings / 1048576:.1f} MiB")
    print(f"Report: {report}")
    return summary

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("--quality", type=float, default=4.0)
    parser.add_argument("--min-bytes", type=int, default=262144)
    args = parser.parse_args()
    if not -1.0 <= args.quality <= 10.0:
        raise SystemExit("--quality must be from -1.0 to 10.0")
    optimize_audio(args.project, quality=args.quality, min_bytes=args.min_bytes)

if __name__ == "__main__":
    main()
