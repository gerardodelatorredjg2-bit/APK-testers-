#!/usr/bin/env python3
"""Apply safe local-only and asset-size optimizations to a disposable Godot copy.

Music files are replaced with tiny silent files at the same paths so existing
resource references remain valid. Sound effects and ambience are preserved.
Steam native add-ons are removed. Online join-code routing is forced to LAN.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

AUDIO_EXTS = {".ogg", ".wav", ".mp3", ".m4a", ".flac", ".opus"}
STEAM_DIRS = {"godotsteam", "steam-multiplayer-peer", "steam"}
STEAM_FILE_PATTERNS = ("steam_api", "steamworks", "godotsteam")
IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
SHADER_UNSUPPORTED_MARKERS = (
    "hint_normal_roughness_texture",
    "hint_depth_texture",
    "hint_screen_texture",
    "sampler2DArray",
    "imageStore(",
    "imageLoad(",
)

def is_music_asset(path: Path, project: Path) -> bool:
    rel = path.relative_to(project)
    if not rel.parts or rel.parts[0].lower() != "audio":
        return False
    return any("music" in part.lower() for part in rel.parts[1:])

def create_silence_file(target: Path, ffmpeg: str) -> None:
    ext = target.suffix.lower()
    codec = {
        ".ogg": ("libvorbis", "ogg"),
        ".opus": ("libopus", "opus"),
        ".wav": ("pcm_s16le", "wav"),
        ".mp3": ("libmp3lame", "mp3"),
        ".m4a": ("aac", "ipod"),
        ".flac": ("flac", "flac"),
    }.get(ext)
    if codec is None:
        raise ValueError(f"Unsupported music container: {target}")
    audio_codec, muxer = codec
    with tempfile.NamedTemporaryFile(prefix="mp-silence-", suffix=ext, dir=target.parent, delete=False) as tmp:
        temp_path = Path(tmp.name)
    try:
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=22050",
            "-t", "0.20", "-map_metadata", "-1", "-vn", "-c:a", audio_codec,
            "-f", muxer, str(temp_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0 or not temp_path.is_file() or temp_path.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg could not create silent {ext}: {result.stderr[-500:]}")
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            temp_path.unlink()

def silence_music(project: Path, ffmpeg: str) -> dict:
    tracks = sorted(p for p in project.rglob("*") if p.is_file()
                    and p.suffix.lower() in AUDIO_EXTS and is_music_asset(p, project))
    before = 0
    after = 0
    details = []
    for track in tracks:
        old_size = track.stat().st_size
        before += old_size
        create_silence_file(track, ffmpeg)
        new_size = track.stat().st_size
        after += new_size
        details.append({
            "path": track.relative_to(project).as_posix(),
            "before_bytes": old_size,
            "after_bytes": new_size,
            "saved_bytes": old_size - new_size,
        })
    return {
        "music_tracks_silenced": len(tracks),
        "music_before_bytes": before,
        "music_after_bytes": after,
        "music_saved_bytes": before - after,
        "tracks": details,
    }

def remove_steam_native_files(project: Path) -> dict:
    removed_files = 0
    removed_bytes = 0
    removed_paths = []
    native_suffixes = {".gdextension", ".so", ".dll", ".dylib", ".a", ".lib", ".aar", ".framework"}
    for path in list(project.rglob("*")):
        if not path.is_file() or ".godot" in path.parts:
            continue
        lower_name = path.name.lower()
        lower_path = str(path).lower()
        in_steam_addon = any(part.lower() in STEAM_DIRS for part in path.relative_to(project).parts)
        is_native_binary = (
            path.suffix.lower() in native_suffixes
            and (
                "steam" in lower_path
                or "godotsteam" in lower_path
                or "steam-multiplayer-peer" in lower_path
            )
        )
        if is_native_binary or (path.suffix.lower() == ".gdextension" and in_steam_addon):
            try:
                size = path.stat().st_size
                path.unlink()
                removed_files += 1
                removed_bytes += size
                removed_paths.append(path.relative_to(project).as_posix())
            except FileNotFoundError:
                pass
    # Remove only directories made empty by deleting native binaries.
    for directory in sorted((p for p in project.rglob("*") if p.is_dir()),
                            key=lambda p: len(p.parts), reverse=True):
        if ".godot" in directory.parts:
            continue
        try:
            directory.rmdir()
        except OSError:
            pass
    return {
        "steam_native_files_removed": removed_files,
        "steam_native_paths": removed_paths,
        "steam_bytes_removed": removed_bytes,
    }

def _remove_remote_backend_cases(source: str) -> str:
    lines = source.splitlines(keepends=True)
    output = []
    i = 0
    remote_labels = {"NetworkBackendType.Enet:", "NetworkBackendType.Steam:", "NetworkBackendType.Online:", "NetworkBackendType.Relay:"}
    while i < len(lines):
        line = lines[i]
        if line.strip() in remote_labels:
            indent = len(line) - len(line.lstrip())
            i += 1
            while i < len(lines):
                candidate = lines[i]
                if candidate.strip() and not candidate.lstrip().startswith("#"):
                    candidate_indent = len(candidate) - len(candidate.lstrip())
                    if candidate_indent <= indent:
                        break
                i += 1
            continue
        output.append(line)
        i += 1
    return "".join(output)

def _resource_referenced_elsewhere(project: Path, relative_path: Path, excluded: set[str]) -> bool:
    rel = relative_path.as_posix()
    tokens = {rel, "res://" + rel}
    uid_file = project / (rel + ".uid")
    if uid_file.is_file():
        uid = uid_file.read_text(encoding="utf-8", errors="replace").strip()
        if uid.startswith("uid://"):
            tokens.add(uid)
    for candidate in project.rglob("*"):
        if not candidate.is_file() or ".godot" in candidate.parts:
            continue
        other_rel = candidate.relative_to(project).as_posix()
        if other_rel in excluded or other_rel == rel or other_rel == rel + ".uid":
            continue
        if candidate.suffix.lower() not in {".gd", ".tscn", ".tres", ".godot", ".cfg", ".import"}:
            continue
        try:
            body = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(token in body for token in tokens):
            return True
    return False

def _patch_local_achievement_backend(project: Path) -> dict:
    """Redirect Steam achievement references to the local backend.

    Some recovered projects define the local backend with a global class;
    others expose only a script resource. Support both forms. When there is no
    global class, add a local preload alias in every GDScript that used the
    Steam global so construction and type annotations remain resolvable.
    """
    manager = project / "autoloads/achievement_manager.gd"
    local_backend = project / "modules/achievements/backends/achievement_backend_local.gd"
    local_backend_rel = "modules/achievements/backends/achievement_backend_local.gd"
    steam_backend_rel = "modules/achievements/backends/achievement_backend_steam.gd"
    if not manager.is_file():
        return {"backend_class": None, "backend_uses_preload": False, "patched_files": []}
    if not local_backend.is_file():
        raise FileNotFoundError(f"Local achievements backend not found: {local_backend}")

    local_source = local_backend.read_text(encoding="utf-8", errors="replace")
    class_match = re.search(
        r"(?m)^class_name\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:#.*)?$",
        local_source,
    )
    uses_preload = class_match is None
    local_class = (
        class_match.group(1)
        if class_match
        else "_MachinePartyLocalAchievementBackend"
    )
    resource_path = "res://" + local_backend_rel
    preload_declaration = f'const {local_class} = preload("{resource_path}")'

    def add_preload_alias(source: str) -> str:
        if not uses_preload or re.search(
            rf"(?m)^\s*const\s+{re.escape(local_class)}\s*=", source
        ):
            return source
        lines = source.splitlines(keepends=True)
        header_indices = [
            i for i, line in enumerate(lines)
            if re.match(r"^\s*(?:extends|class_name)\s+", line)
        ]
        insert_at = max(header_indices) + 1 if header_indices else 0
        insertion = preload_declaration + "\n"
        if insert_at > 0 and lines[insert_at - 1].strip():
            insertion = "\n" + insertion
        if insert_at < len(lines) and lines[insert_at].strip():
            insertion += "\n"
        lines.insert(insert_at, insertion)
        return "".join(lines)

    patched_files = []
    for path in project.rglob("*"):
        if not path.is_file() or ".godot" in path.relative_to(project).parts:
            continue
        relative = path.relative_to(project).as_posix()
        if relative == steam_backend_rel:
            # Do not rewrite the Steam backend's own declaration.
            continue
        if path.suffix.lower() not in {".gd", ".tscn", ".tres", ".godot", ".cfg", ".import"}:
            continue
        try:
            original = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        updated = original.replace(
            "achievement_backend_steam.gd",
            "achievement_backend_local.gd",
        )
        if path.suffix.lower() == ".gd":
            updated = re.sub(r"\bAchievementBackendSteam\b", local_class, updated)
            if local_class in updated:
                updated = add_preload_alias(updated)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            patched_files.append(relative)

    manager_text = manager.read_text(encoding="utf-8", errors="replace")
    if "AchievementBackendSteam" in manager_text or "achievement_backend_steam.gd" in manager_text:
        raise RuntimeError(
            "Steam achievement backend reference remains in achievement_manager.gd "
            "after local backend redirection."
        )
    # Fail early if any non-Steam script still depends on the removed global.
    unresolved = []
    for path in project.rglob("*.gd"):
        if not path.is_file() or path.relative_to(project).as_posix() == steam_backend_rel:
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "AchievementBackendSteam" in body:
            unresolved.append(path.relative_to(project).as_posix())
    if unresolved:
        raise RuntimeError(
            "Unpatched Steam achievement class references remain: "
            + ", ".join(unresolved[:20])
        )
    return {
        "backend_class": local_class,
        "backend_uses_preload": uses_preload,
        "patched_files": patched_files,
    }

def _remove_unreferenced_remote_code(project: Path) -> list[str]:
    # Fix both the old script-path reference and the global class identifier
    # before evaluating which Steam/relay scripts are safe to delete.
    _patch_local_achievement_backend(project)
    candidates = [
        "modules/multiplayer/backends/enet_backend.gd",
        "modules/multiplayer/backends/steam_backend.gd",
        "modules/multiplayer/backends/relay_backend.gd",
        "modules/multiplayer/backends/relay_multiplayer_peer.gd",
        "modules/achievements/backends/achievement_backend_steam.gd",
    ]
    candidate_set = set(candidates) | {p + ".uid" for p in candidates}
    removed = []
    for rel in candidates:
        target = project / rel
        if not target.is_file():
            continue
        if _resource_referenced_elsewhere(project, Path(rel), candidate_set):
            continue
        target.unlink()
        sidecar = project / (rel + ".uid")
        if sidecar.is_file():
            sidecar.unlink()
        removed.append(rel)
    return removed

def _disable_legacy_online_menu(project: Path) -> bool:
    menu_script = project / "modules/multiplayer_lobby/multiplayer_menu.gd"
    if not menu_script.is_file():
        return False
    old = menu_script.read_text(encoding="utf-8", errors="replace")
    extends_match = re.search(r"(?m)^extends\s+([A-Za-z0-9_.]+)\s*$", old)
    class_match = re.search(r"(?m)^class_name\s+[A-Za-z0-9_]+\s*$", old)
    extends_line = extends_match.group(0) if extends_match else "extends Node"
    class_line = class_match.group(0) if class_match else ""
    # The old online/Steam join UI is superseded by the LAN HOST/JOIN overlay.
    # Keep the resource path and global class identity, but make this legacy
    # screen inert so it cannot initiate relay, invite or Steam flows.
    stub = extends_line + "\n"
    if class_line:
        stub += class_line + "\n"
    stub += "\nfunc _ready() -> void:\n\tqueue_free()\n"
    menu_script.write_text(stub, encoding="utf-8")
    return True

def _remove_unreferenced_shape_cutter_test(project: Path) -> list[str]:
    scene_rel = "minigames/shape_cutter/shape_cutter_test.tscn"
    script_rel = "minigames/shape_cutter/scripts/shape_cutter_test.gd"
    candidates = {scene_rel, script_rel, script_rel + ".uid"}
    if _resource_referenced_elsewhere(project, Path(scene_rel), candidates):
        return []
    if _resource_referenced_elsewhere(project, Path(script_rel), candidates):
        return []
    removed = []
    for rel in (scene_rel, script_rel):
        target = project / rel
        if target.is_file():
            target.unlink()
            removed.append(rel)
        sidecar = project / (rel + ".uid")
        if sidecar.is_file():
            sidecar.unlink()
    return removed

def force_lan_only(project: Path) -> dict:
    manager = project / "modules/multiplayer/network_manager.gd"
    if not manager.is_file():
        raise FileNotFoundError(f"Network manager not found: {manager}")
    source = manager.read_text(encoding="utf-8")
    start = source.find("func backend_type_for_join_code(")
    if start < 0:
        raise RuntimeError("Could not locate NetworkManager.backend_type_for_join_code()")
    next_func = source.find("\nfunc ", start + 1)
    end = next_func if next_func >= 0 else len(source)
    replacement = (
        "func backend_type_for_join_code(code: String) -> NetworkBackendType:\n"
        "\t# This build accepts only direct local IP:PORT addresses.\n"
        "\treturn NetworkBackendType.Lan\n"
    )
    source = source[:start] + replacement + source[end:]
    source = _remove_remote_backend_cases(source)
    # Reduce the backend enum to local transports only. Stale references in
    # other scripts are redirected to LAN below.
    source, enum_count = re.subn(
        r"enum NetworkBackendType\s*\{[^}]*\}",
        "enum NetworkBackendType{\n\tLan\n}",
        source,
        count=1,
    )
    if enum_count != 1:
        raise RuntimeError("Could not reduce NetworkBackendType to LAN only")
    source = re.sub(
        r"NetworkBackendType\.(?:Enet|Steam|Online|Relay)\b",
        "NetworkBackendType.Lan",
        source,
    )
    # Guard the shutdown path: Godot throws if get_unique_id() is queried after
    # the LAN peer has already been closed during scene teardown.
    if "multiplayer.get_unique_id()" in source:
        source = source.replace("multiplayer.get_unique_id()", "_local_safe_multiplayer_unique_id()")
        if "func _local_safe_multiplayer_unique_id()" not in source:
            source = source.rstrip() + (
                "\n\nfunc _local_safe_multiplayer_unique_id() -> int:\n"
                "\tvar peer: MultiplayerPeer = multiplayer.multiplayer_peer\n"
                "\tif peer == null or peer.get_connection_status() == MultiplayerPeer.CONNECTION_DISCONNECTED:\n"
                "\t\treturn 1\n"
                "\treturn multiplayer.get_unique_id()\n"
            )
    manager.write_text(source, encoding="utf-8")

    patched_scripts = []
    for path in project.rglob("*.gd"):
        if ".godot" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        new_text = re.sub(
            r"NetworkManager\.NetworkBackendType\.(?:Enet|Steam|Online|Relay)\b",
            "NetworkManager.NetworkBackendType.Lan",
            text,
        )
        new_text = re.sub(
            r"(?m)^(\s*NetworkManager\.active_backend_type\s*=\s*)NetworkManager\.NetworkBackendType\.Enet\s*$",
            r"\1NetworkManager.NetworkBackendType.Lan",
            new_text,
        )
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            patched_scripts.append(path.relative_to(project).as_posix())

    lobby = project / "scenes/lobby/scripts/lobby_scene.gd"
    if lobby.is_file():
        text = lobby.read_text(encoding="utf-8")
        pattern = re.compile(
            r"(?m)^\tif GameManager\.lan_multiplayer_enabled:\n"
            r"\t\tNetworkManager\.active_backend_type\s*=\s*NetworkManager\.NetworkBackendType\.Lan\n"
            r"\telse:\n"
            r"\t\tNetworkManager\.active_backend_type\s*=\s*NetworkManager\.NetworkBackendType\.(?:Relay|Online|Steam|Enet)"
        )
        text2, count = pattern.subn(
            "\tNetworkManager.active_backend_type = NetworkManager.NetworkBackendType.Lan",
            text,
        )
        if count:
            lobby.write_text(text2, encoding="utf-8")
            patched_scripts.append(lobby.relative_to(project).as_posix())

    # Disable the obsolete remote join UI first, so it no longer keeps
    # Steam/relay backend scripts artificially referenced during cleanup.
    legacy_menu_disabled = _disable_legacy_online_menu(project)
    remote_code_removed = _remove_unreferenced_remote_code(project)
    shape_test_removed = _remove_unreferenced_shape_cutter_test(project)
    manager_text = manager.read_text(encoding="utf-8")
    if "return NetworkBackendType.Lan" not in manager_text:
        raise RuntimeError("LAN-only join-code routing was not applied")
    return {
        "network_mode": "LAN_ONLY",
        "remote_join_code_fallback_disabled": True,
        "remote_backend_cases_removed": all(
            token not in manager_text
            for token in (
                "NetworkBackendType.Steam",
                "NetworkBackendType.Online",
                "NetworkBackendType.Relay",
            )
        ),
        "remote_backend_scripts_removed": remote_code_removed,
        "legacy_online_menu_disabled": legacy_menu_disabled,
        "unreferenced_debug_test_resources_removed": shape_test_removed,
        "local_network_shutdown_guard_added": "_local_safe_multiplayer_unique_id()" in manager_text,
        "local_routing_scripts_patched": sorted(set(patched_scripts)),
        "android_internet_permission_retained_for_lan_sockets": True,
    }

def optimize_images(project: Path) -> dict:
    png_tool = shutil.which("optipng")
    jpeg_tool = shutil.which("jpegoptim")
    before = after = processed = 0
    errors = []
    files = sorted(p for p in project.rglob("*") if p.is_file()
                   and p.suffix.lower() in IMAGE_EXTS and ".godot" not in p.parts)
    for path in files:
        old_size = path.stat().st_size
        before += old_size
        try:
            if path.suffix.lower() == ".png" and png_tool:
                subprocess.run([png_tool, "-quiet", "-o", "2", str(path)],
                               capture_output=True, text=True, check=False)
            elif path.suffix.lower() in {".jpg", ".jpeg"} and jpeg_tool:
                subprocess.run([jpeg_tool, "--strip-all", "--preserve", "--quiet", str(path)],
                               capture_output=True, text=True, check=False)
            processed += 1
        except OSError as exc:
            errors.append(f"{path.relative_to(project).as_posix()}: {exc}")
        after += path.stat().st_size
    return {
        "image_files_seen": len(files),
        "image_files_processed": processed,
        "image_before_bytes": before,
        "image_after_bytes": after,
        "image_saved_bytes": before - after,
        "lossless_png_optimizer_available": bool(png_tool),
        "jpeg_metadata_stripper_available": bool(jpeg_tool),
        "image_optimizer_errors": errors,
    }

def optimize_glb_files(project: Path) -> dict:
    cli = shutil.which("gltf-transform")
    models = sorted(p for p in project.rglob("*.glb") if ".godot" not in p.parts)
    report = {
        "glb_files_seen": len(models),
        "glb_files_optimized": 0,
        "glb_files_kept_original": 0,
        "glb_before_bytes": 0,
        "glb_after_bytes": 0,
        "glb_saved_bytes": 0,
        "glb_errors": [],
        "optimizer_available": bool(cli),
        "method": "lossless animation resampling and sparse zero-data encoding",
    }
    if not cli:
        report["glb_files_kept_original"] = len(models)
        report["glb_after_bytes"] = sum(p.stat().st_size for p in models)
        report["glb_before_bytes"] = report["glb_after_bytes"]
        return report

    for source in models:
        original_size = source.stat().st_size
        report["glb_before_bytes"] += original_size
        with tempfile.TemporaryDirectory(prefix="mp-glb-", dir=source.parent) as folder:
            current = source
            previous = None
            try:
                for operation in ("resample", "sparse"):
                    output = Path(folder) / f"{operation}.glb"
                    result = subprocess.run(
                        [cli, operation, str(current), str(output)],
                        capture_output=True, text=True, check=False,
                    )
                    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
                        raise RuntimeError(
                            f"{operation} failed: {(result.stderr or result.stdout)[-700:]}"
                        )
                    current = output
                validation = subprocess.run(
                    [cli, "validate", str(current)],
                    capture_output=True, text=True, check=False,
                )
                if validation.returncode != 0:
                    raise RuntimeError(
                        f"glTF validation failed: {(validation.stderr or validation.stdout)[-1000:]}"
                    )
                final_size = current.stat().st_size
                if final_size < original_size:
                    temp_final = source.with_name(source.name + ".lean-tmp")
                    shutil.copy2(current, temp_final)
                    os.replace(temp_final, source)
                    report["glb_files_optimized"] += 1
                    report["glb_after_bytes"] += final_size
                else:
                    report["glb_files_kept_original"] += 1
                    report["glb_after_bytes"] += original_size
            except Exception as exc:
                report["glb_files_kept_original"] += 1
                report["glb_after_bytes"] += original_size
                report["glb_errors"].append({
                    "path": source.relative_to(project).as_posix(),
                    "error": str(exc),
                })
    report["glb_saved_bytes"] = report["glb_before_bytes"] - report["glb_after_bytes"]
    return report

def asset_audit(project: Path) -> dict:
    large_models = []
    large_scenes = []
    shader_flags = []
    missing_scene_resources = []
    for path in project.rglob("*"):
        if not path.is_file() or ".godot" in path.parts:
            continue
        rel = path.relative_to(project).as_posix()
        size = path.stat().st_size
        if path.suffix.lower() == ".glb" and size >= 8 * 1024 * 1024:
            large_models.append({"path": rel, "bytes": size})
        if path.suffix.lower() in {".tscn", ".tres"} and size >= 2 * 1024 * 1024:
            large_scenes.append({"path": rel, "bytes": size})
        if path.suffix.lower() in {".gdshader", ".shader"}:
            shader = path.read_text(encoding="utf-8", errors="replace")
            found = [marker for marker in SHADER_UNSUPPORTED_MARKERS if marker in shader]
            if found:
                shader_flags.append({"path": rel, "compatibility_markers": found})
        if path.suffix.lower() in {".tscn", ".tres"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            for resource in re.findall(r'path="res://([^"]+)"', text):
                if not (project / resource).exists():
                    missing_scene_resources.append({"scene": rel, "resource": resource})
    return {
        "large_glb_models_over_8_mib": large_models,
        "large_scenes_over_2_mib": large_scenes,
        "shader_compatibility_review": shader_flags,
        "missing_external_scene_resources": missing_scene_resources[:200],
    }

def register_graphics_autoload(project: Path) -> None:
    script = project / "mobile_graphics_settings.gd"
    script.write_text(r'''extends CanvasLayer

const SETTINGS_PATH := "user://machine_party_mobile_graphics.cfg"
var _quality := 1
var _root: Control
var _panel: PanelContainer
var _selector: OptionButton

func _ready() -> void:
	layer = 90
	_load_quality()
	_build_ui()
	_apply_quality()

func _load_quality() -> void:
	var config := ConfigFile.new()
	if config.load(SETTINGS_PATH) == OK:
		_quality = clampi(int(config.get_value("graphics", "quality", 1)), 0, 2)

func _build_ui() -> void:
	_root = Control.new()
	_root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_root)

	var button := Button.new()
	button.text = "GFX"
	button.tooltip_text = "Graphics quality"
	button.position = Vector2(12, 12)
	button.custom_minimum_size = Vector2(70, 42)
	button.z_index = 100
	_root.add_child(button)
	button.pressed.connect(_toggle_panel)

	_panel = PanelContainer.new()
	_panel.position = Vector2(12, 60)
	_panel.custom_minimum_size = Vector2(250, 124)
	_panel.z_index = 101
	_panel.visible = false
	_root.add_child(_panel)

	var layout := VBoxContainer.new()
	layout.add_theme_constant_override("separation", 6)
	_panel.add_child(layout)

	var title := Label.new()
	title.text = "GRAPHICS QUALITY"
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	layout.add_child(title)

	_selector = OptionButton.new()
	_selector.add_item("Performance (low)", 0)
	_selector.add_item("Balanced", 1)
	_selector.add_item("Visual quality (high)", 2)
	_selector.select(_quality)
	layout.add_child(_selector)
	_selector.item_selected.connect(_on_quality_selected)

	var close := Button.new()
	close.text = "CLOSE"
	close.pressed.connect(_toggle_panel)
	layout.add_child(close)

func _toggle_panel() -> void:
	_panel.visible = not _panel.visible

func _on_quality_selected(index: int) -> void:
	_quality = clampi(index, 0, 2)
	var config := ConfigFile.new()
	config.set_value("graphics", "quality", _quality)
	config.save(SETTINGS_PATH)
	_apply_quality()

func _apply_quality() -> void:
	var viewport := get_viewport()
	match _quality:
		0:
			viewport.scaling_3d_scale = 0.65
			viewport.msaa_3d = Viewport.MSAA_DISABLED
			viewport.screen_space_aa = Viewport.SCREEN_SPACE_AA_DISABLED
		1:
			viewport.scaling_3d_scale = 0.82
			viewport.msaa_3d = Viewport.MSAA_DISABLED
			viewport.screen_space_aa = Viewport.SCREEN_SPACE_AA_DISABLED
		_:
			viewport.scaling_3d_scale = 1.0
			viewport.msaa_3d = Viewport.MSAA_DISABLED
			viewport.screen_space_aa = Viewport.SCREEN_SPACE_AA_DISABLED
''', encoding="utf-8")

    cfg = project / "project.godot"
    text = cfg.read_text(encoding="utf-8")
    name = 'MobileGraphics="*res://mobile_graphics_settings.gd"'
    if name not in text:
        match = re.search(r"(?ms)^\[autoload\]\n(.*?)(?=^\[|\Z)", text)
        if match:
            end = match.end(1)
            text = text[:end] + name + "\n" + text[end:]
        else:
            text = text.rstrip() + "\n\n[autoload]\n" + name + "\n"
        cfg.write_text(text, encoding="utf-8")

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    if not (project / "project.godot").is_file():
        raise SystemExit(f"Godot project not found: {project}")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg is required to replace music with silent placeholders")
    music = silence_music(project, ffmpeg)
    steam = remove_steam_native_files(project)
    local = force_lan_only(project)
    images = optimize_images(project)
    glbs = optimize_glb_files(project)
    register_graphics_autoload(project)
    audit = asset_audit(project)
    report = {
        "music": music,
        "steam": steam,
        "network": local,
        "images": images,
        "glb_models": glbs,
        "asset_audit": audit,
        "graphics_profiles": ["Performance (low)", "Balanced", "Visual quality (high)"],
        "notes": [
            "Music tracks are silent placeholders; sound effects and ambience are retained.",
            "Android INTERNET permission stays enabled because Android requires it for local UDP/ENet sockets.",
            "PNG optimization is lossless; JPEG optimization strips metadata without re-encoding image pixels.",
            "GLB geometry is not decimated automatically to avoid breaking rigging, animations or collision shapes.",
            "Shader flags are reported for inspection; unsafe blanket shader rewrites are not applied.",
        ],
    }
    (project / "lean_local_optimization.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("LOCAL_ONLY_OPTIMIZATION_PASS")
    print(f"Music: silenced {music['music_tracks_silenced']} tracks; saved {music['music_saved_bytes'] / 1048576:.1f} MiB.")
    print(f"Steam native components removed: {steam['steam_native_files_removed']} files, {steam['steam_bytes_removed'] / 1048576:.2f} MiB.")
    print(f"Images: {images['image_files_processed']} processed; saved {images['image_saved_bytes'] / 1048576:.2f} MiB.")
    print(f"GLB: {glbs['glb_files_seen']} inspected, {glbs['glb_files_optimized']} losslessly optimized; saved {glbs['glb_saved_bytes'] / 1048576:.2f} MiB.")
    print(f"Network mode: {local['network_mode']}; mobile graphics profiles: 3.")
    print(f"Shader compatibility items flagged: {len(audit['shader_compatibility_review'])}")
    print(f"Missing scene resources: {len(audit['missing_external_scene_resources'])}")

if __name__ == "__main__":
    main()
