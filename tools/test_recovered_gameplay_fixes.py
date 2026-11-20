#!/usr/bin/env python3
"""Verify targeted mobile gameplay fixes were applied to the recovered project."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: test_recovered_gameplay_fixes.py PROJECT_DIR")

root = Path(sys.argv[1]).resolve()

def require(path: str, text: str, label: str) -> None:
    source = (root / path).read_text(encoding="utf-8")
    if text not in source:
        raise SystemExit(f"FAIL: {label} missing from {path}")
    print(f"PASS: {label}")

player = "minigames/exploding_collar_race/components/player/scripts/exploding_collar_race_player.gd"
require(player, "animation_tree.active = true", "base locomotion AnimationTree enabled")
require(player, "torso_animation_tree.active = true", "injured crawl AnimationTree enabled")

chisel = "minigames/chisel_gauntlet_multiplayer/chisel_gauntlet.tscn"
require(
    chisel,
    '[node name="ColorRect" type="ColorRect" parent="SubViewport_jumbotron feed/CanvasLayer/BackBufferCopy"]\nvisible = false',
    "chisel reference monitor is not covered by CRT screen-texture overlay",
)
require(chisel, "render_target_update_mode = 4", "jumbotron 3D viewport continuously refreshes")
require(chisel, "texture = SubResource(\"ViewportTexture_dwdui\")", "local jumbotron viewport texture remains assigned")

mine = "minigames/exploding_collar_race/exploding_collar_race.tscn"
require(
    mine,
    '[node name="ColorRect" type="ColorRect" parent="post processing/posterization/BackBufferCopy"]\nvisible = false',
    "mine-game fullscreen posterization overlay disabled for Android",
)

overlay = "modules/global_overlay/global_overlay.gd"
require(overlay, "var fade_generation: int = 0", "transition fade generation guard added")
require(overlay, "overlay_tween.kill()", "previous fade tween cancelled")
fade_source = (root / overlay).read_text(encoding="utf-8")
if "await get_tree().physics_frame" in fade_source:
    raise SystemExit("FAIL: unsafe early fade_finished emission remains")
print("PASS: overlapping fades no longer signal a false completion")


mine_effect = "prefabs/managed_effects/mine_explosion.tscn"
effect_source = (root / mine_effect).read_text(encoding="utf-8")
require(mine_effect, '[sub_resource type="QuadMesh" id="QuadMesh_om174"]\nsize = Vector2(6, 6)', "mine smoke billboards reduced from 16m to 6m")
require(mine_effect, 'scale_max = 1.0\nscale_curve = SubResource("CurveTexture_0irmn")', "mine up-smoke scale capped")
require(mine_effect, 'scale_max = 1.0\nscale_curve = SubResource("CurveTexture_0alh5")', "mine ring-smoke scale capped")
print("RECOVERED_GAMEPLAY_FIXES_PASS")
