from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

PROFILES = {
    "low": {"scale": 0.50, "fps": 30, "max_lights": 2},
    "balanced": {"scale": 0.65, "fps": 45, "max_lights": 3},
    "high": {"scale": 0.75, "fps": 60, "max_lights": 4},
}
COMMON_SETTINGS = {
    "rendering/anti_aliasing/quality/msaa_3d": "0",
    "rendering/anti_aliasing/quality/screen_space_aa": "0",
    "rendering/environment/ssao/quality": "0",
    "rendering/environment/ssil/quality": "0",
    "rendering/environment/screen_space_reflection/roughness_quality": "0",
}

# Static, high-polygon environments with LOD generation disabled in the recovered
# project. LODs are enabled only on these verified non-animated scene assets.
# Animated characters, VFX, collision meshes and small props are intentionally omitted.
STATIC_ENVIRONMENT_LOD_IMPORTS = (
    "minigames/knife_at_the_office/models/kato office art1/kato office art1.glb.import",
    "minigames/junk_platform/models/junk platform visuals2/junk platform visuals2.glb.import",
    "minigames/knife_at_the_office/models/kato office4/kato office4.glb.import",
    "models/tower scene export2/tower scene export2.glb.import",
    "minigames/duck_hunt/models/duck hunt environment2/duck hunt environment2.glb.import",
    "minigames/spine_breaker/models/spinebreaker visuals2/spinebreaker visuals2.glb.import",
    "minigames/duck_hunt/models/duck hunt covers revised1/duck hunt covers revised1.glb.import",
    "minigames/escalator_pit/models/escalator pit fence1/escalator pit fence1.glb.import",
)


def atomic_write(path: Path, content: str) -> None:
    """Write text atomically so an interrupted optimizer cannot truncate a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def set_project_setting(lines, section, key, value):
    header = f"[{section}]"
    section_starts = [i for i, line in enumerate(lines) if line.strip() == header]
    if not section_starts:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([header, f"{key}={value}"])
        return

    ranges = []
    for start in section_starts:
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[") and lines[i].strip().endswith("]")),
            len(lines),
        )
        ranges.append((start, end))
    pattern = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
    matches = [i for start, end in ranges for i in range(start + 1, end) if pattern.match(lines[i])]
    if matches:
        lines[matches[0]] = f"{key}={value}"
        for duplicate in reversed(matches[1:]):
            del lines[duplicate]
    else:
        start = ranges[0][0]
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[") and lines[i].strip().endswith("]")),
            len(lines),
        )
        lines.insert(end, f"{key}={value}")


def replace_typed_assignment(source, name, value):
    pattern = re.compile(
        rf"(?m)^(\s*(?:@export\s+)?var\s+{re.escape(name)}(?:\s*:\s*[^=]+)?\s*=\s*)"
        rf"[0-9]+(?:\.[0-9]+)?(\s*(?:#.*)?)$"
    )
    return pattern.subn(rf"\g<1>{value}\g<2>", source)


def apply(root, profile_name, dry_run=False):
    if profile_name not in PROFILES:
        raise ValueError(f"Unknown profile {profile_name}; choose low, balanced, high")
    project = root / "project.godot"
    if not project.is_file():
        raise FileNotFoundError(f"project.godot missing: {project}")
    profile = PROFILES[profile_name]
    scale = float(profile["scale"])
    fps = int(profile["fps"])
    lights = int(profile["max_lights"])
    settings = dict(COMMON_SETTINGS)
    settings.update({"rendering/scaling_3d/scale": str(scale), "application/run/max_fps": str(fps)})
    lines = project.read_text(encoding="utf-8").splitlines()
    for key, value in settings.items():
        section, setting = key.split("/", 1)
        set_project_setting(lines, section, setting, value)
    project_content = "\n".join(lines) + "\n"
    changed = []
    pending_writes = {}
    lod_imports_enabled = []
    lod_imports_missing = []
    for relative_path in STATIC_ENVIRONMENT_LOD_IMPORTS:
        import_path = root / relative_path
        if not import_path.is_file():
            lod_imports_missing.append(relative_path)
            continue
        source = import_path.read_text(encoding="utf-8")
        updated, count = re.subn(
            r"(?m)^(meshes/generate_lods\\s*=\\s*)false\\s*$",
            r"\\1true",
            source,
        )
        if count:
            pending_writes[import_path] = updated
            lod_imports_enabled.append(relative_path)
        elif re.search(r"(?m)^meshes/generate_lods\\s*=\\s*true\\s*$", source):
            continue
        else:
            raise ValueError(f"{relative_path}: missing meshes/generate_lods import option")
    if project_content != project.read_text(encoding="utf-8"):
        pending_writes[project] = project_content

    allowed = {"mobilegamesettings", "mobileboost", "mobilelightoptimizer", "allinonemobileoptimizer"}
    for path in root.rglob("*.gd"):
        if ".godot" in path.parts or path.stem.lower() not in allowed or path.is_symlink():
            continue
        source = path.read_text(encoding="utf-8")
        updated = source
        for name, val in (("scaling_3d_scale", scale), ("target_mobile_fps", fps), ("max_lights", lights)):
            updated, _ = replace_typed_assignment(updated, name, val)
        if updated != source:
            changed.append(str(path.relative_to(root)))
            pending_writes[path] = updated

    report = {
        "profile": profile_name,
        "render_scale": scale,
        "fps_cap": fps,
        "max_dynamic_lights": lights,
        "disabled_quality_features": ["MSAA 3D", "screen-space AA", "SSAO", "SSIL", "SSR quality"],
        "runtime_optimizer_files_changed": sorted(changed),
        "static_environment_lod_imports_enabled": sorted(lod_imports_enabled),
        "static_environment_lod_imports_missing": sorted(lod_imports_missing),
        "files_to_write": sorted(str(path.relative_to(root)) for path in pending_writes),
        "dry_run": bool(dry_run),
        "note": "Static profile, not dynamic runtime adaptation. Settings require device testing.",
    }
    if not dry_run:
        for path, content in pending_writes.items():
            atomic_write(path, content)
        report_path = Path(os.environ.get("MOBILE_PERFORMANCE_REPORT", root.parent / "mobile-performance-report.json"))
        atomic_write(report_path, json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description="Apply a mobile rendering/performance profile safely.")
    parser.add_argument("project", type=Path)
    parser.add_argument("--profile", choices=sorted(PROFILES), default=os.environ.get("MOBILE_PROFILE", "low"))
    parser.add_argument("--dry-run", action="store_true", help="Report planned changes without writing any files.")
    args = parser.parse_args()
    try:
        print(json.dumps(apply(args.project.resolve(), args.profile, dry_run=args.dry_run), indent=2))
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
