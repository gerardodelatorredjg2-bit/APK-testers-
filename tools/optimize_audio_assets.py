#!/usr/bin/env python3
"""Re-encode source OGG audio on a disposable build copy, keeping smaller output only.

The original recovery artifact is never modified. Godot sidecar import settings
and resource paths stay unchanged because each file keeps its original name.
"""
from __future__ import annotations
import argparse
import csv
import os
import shutil
import subprocess
from pathlib import Path

def optimize_audio(project: Path, quality: float = 4.0, min_bytes: int = 262144) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg was not found; install it before audio optimization.")
    project = project.resolve()
    if not (project / "project.godot").is_file():
        raise ValueError(f"Not a Godot project: {project}")
    rows = []
    before_total = after_total = saved_total = 0
    for source in sorted(project.rglob("*.ogg")):
        if ".godot" in source.relative_to(project).parts:
            continue
        input_bytes = source.stat().st_size
        before_total += input_bytes
        if input_bytes < min_bytes:
            after_total += input_bytes
            rows.append((source.relative_to(project).as_posix(), input_bytes, input_bytes, 0, "skipped_small", quality))
            continue
        temporary = source.with_name(source.name + ".lean-tmp.ogg")
        try:
            proc = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(source), "-map", "0:a:0", "-map_metadata", "0", "-vn",
                    "-c:a", "libvorbis", "-q:a", str(quality), str(temporary),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0 or not temporary.is_file():
                detail = (proc.stderr or "ffmpeg produced no output").strip().replace("\n", " ")[:300]
                if temporary.exists():
                    temporary.unlink()
                after_total += input_bytes
                rows.append((source.relative_to(project).as_posix(), input_bytes, input_bytes, 0, "kept_encode_failed:" + detail, quality))
                continue
            output_bytes = temporary.stat().st_size
            if output_bytes < input_bytes:
                os.replace(temporary, source)
                saved = input_bytes - output_bytes
                after_total += output_bytes
                saved_total += saved
                decision = "replaced_smaller"
            else:
                temporary.unlink()
                after_total += input_bytes
                output_bytes = input_bytes
                saved = 0
                decision = "kept_original_not_smaller"
            rows.append((source.relative_to(project).as_posix(), input_bytes, output_bytes, saved, decision, quality))
        except Exception:
            if temporary.exists():
                temporary.unlink()
            raise

    report = project / "lean_audio_optimization.csv"
    with report.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["path", "input_bytes", "output_bytes", "saved_bytes", "decision", "vorbis_quality"])
        writer.writerows(rows)
    summary = {
        "tracks": len(rows),
        "input_bytes": before_total,
        "output_bytes": after_total,
        "saved_bytes": saved_total,
        "saved_percent": round(saved_total * 100.0 / before_total, 2) if before_total else 0.0,
        "report": str(report),
    }
    print(
        "OGG optimization: {tracks} tracks, {before:.1f} MiB -> {after:.1f} MiB, "
        "saved {saved:.1f} MiB ({percent:.2f}%).".format(
            tracks=len(rows),
            before=before_total / 1048576,
            after=after_total / 1048576,
            saved=saved_total / 1048576,
            percent=summary["saved_percent"],
        )
    )
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
