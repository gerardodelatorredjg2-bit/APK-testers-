#!/usr/bin/env python3
"""Apply verified gameplay bug fixes to the recovered Machine Party project."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()

def replace_once(rel, old, new):
    path = root / rel
    text = path.read_text(encoding="utf-8")
    if new in text:
        print(f"SKIP already applied: {rel}")
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{rel}: expected one target, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"PATCHED: {rel}")

replace_once(
    "modules/multiplayer/network_manager.gd",
    "func _exit_tree() -> void :\n\n\ton_peer_disconnected.emit(multiplayer.get_unique_id())\n",
    "func _exit_tree() -> void :\n\n\t# ENet may already have been closed during backend cleanup.\n\tif not is_active():\n\t\treturn\n\n\ton_peer_disconnected.emit(multiplayer.get_unique_id())\n",
)
replace_once(
    "minigames/escalator_pit/components/player/scripts/escalator_pit_player.gd",
    "clamp(0.0, 1.5, abs(escalator_speed + speed))",
    "clampf(abs(escalator_speed + speed), 0.0, 1.0)",
)
replace_once(
    "minigames/chisel_gauntlet_multiplayer/scripts/chisel_gauntlet.gd",
    "\tif counter < 0:\n\t\treturn\n\n\tinput_sum /= float(players.size())\n",
    "\tif counter == 0:\n\t\treturn\n\n\t# Average only the players who supplied spectator-camera input.\n\tinput_sum /= float(counter)\n",
)

matcher_path = root / "minigames/shape_cutter/scripts/pattern_matcher.gd"
matcher = matcher_path.read_text(encoding="utf-8")
start = matcher.index("func verify_shape(")
end = matcher.index("\nfunc check_against_pattern(", start)
new_methods = '''func verify_shape(points: Array[Vector2], pattern: Pattern):
\tif points.size() < 3 or not PatternPoints.has(pattern):
\t\treturn 0.0

\tvar pattern_sections: Dictionary[int, int]
\tfor i in range(int(PatternPoints[pattern].size() - 1)):
\t\tpattern_sections[i] = 0

\tvar matched_points: Array[Vector2]
\tvar matched_point_divinations: Array[float]
\tvar pattern_points: Array = PatternPoints[pattern].duplicate()
\tif pattern_scale.has(pattern):
\t\tfor i in pattern_points.size():
\t\t\tpattern_points[i] = pattern_points[i] * pattern_scale[pattern]

\tvar center: Vector2 = Vector2.ZERO
\tfor point in points:
\t\tcenter += point
\tcenter /= float(points.size())
\tvar centered_input: Array[Vector2] = []
\tfor point in points:
\t\tcentered_input.append(point - center)

\tfor i in centered_input.size():
\t\tvar closest_segment: int = -1
\t\tvar closest_match: float = 10.0
\t\tfor j in range(0, pattern_points.size() - 1):
\t\t\tvar from_input := Vector2.ZERO
\t\t\tvar to_input: Vector2 = centered_input[i].normalized()
\t\t\tvar from_pattern: Vector2 = pattern_points[j]
\t\t\tvar to_pattern: Vector2 = pattern_points[j + 1]
\t\t\tvar result = Geometry2D.segment_intersects_segment(
\t\t\t\tfrom_input, to_input, from_pattern, to_pattern
\t\t\t)
\t\t\tif result:
\t\t\t\tvar distance: float = (result - centered_input[i]).length()
\t\t\t\tif distance <= matched_tresshold:
\t\t\t\t\tpattern_sections[j] += 1
\t\t\t\t\tmatched_points.append(centered_input[i])
\t\t\t\t\tmatched_point_divinations.append(distance)
\t\t\t\t\tbreak
\t\t\t\telif distance < closest_match:
\t\t\t\t\tclosest_match = distance
\t\t\t\t\tclosest_segment = j

\tvar average_deviation: float = 0.0
\tif not matched_point_divinations.is_empty():
\t\tfor deviation in matched_point_divinations:
\t\t\taverage_deviation += deviation
\t\taverage_deviation /= float(matched_point_divinations.size())

\tvar low_tolerance: float = average_deviation - divination_tolerance
\tvar high_tolerance: float = average_deviation + divination_tolerance
\tvar within_tolerance: int = 0
\tfor deviation in matched_point_divinations:
\t\tif deviation >= low_tolerance and deviation <= high_tolerance:
\t\t\twithin_tolerance += 1

\tvar score: float = (float(within_tolerance) / float(points.size())) * 100.0
\tvar section_penalty: float = 100.0 / float(pattern_sections.size())
\tfor section in pattern_sections.keys():
\t\tif pattern_sections[section] <= 0:
\t\t\tscore = max(score - section_penalty, 0.0)
\treturn score

func verify_circle(points: Array[Vector2]):
\tif points.size() < 8:
\t\treturn 0.0

\tvar center: Vector2 = Vector2.ZERO
\tfor point in points:
\t\tcenter += point
\tcenter /= float(points.size())

\tvar centered_input: Array[Vector2] = []
\tvar angles: Array[float] = []
\tvar average_distance: float = 0.0
\tfor point in points:
\t\tvar centered: Vector2 = point - center
\t\tcentered_input.append(centered)
\t\taverage_distance += centered.length()
\t\tangles.append(fposmod(atan2(centered.y, centered.x), TAU))
\taverage_distance /= float(centered_input.size())
\tif average_distance <= 0.000001:
\t\treturn 0.0

\tangles.sort()
\tvar largest_gap: float = angles[0] + TAU - angles[-1]
\tfor i in range(1, angles.size()):
\t\tlargest_gap = max(largest_gap, angles[i] - angles[i - 1])
\tvar angular_coverage: float = clampf(1.0 - largest_gap / TAU, 0.0, 1.0)

\tvar matched: int = 0
\tfor point in centered_input:
\t\tif abs(0.4 - point.length()) <= matched_tresshold:
\t\t\matched += 1
\tvar radial_accuracy: float = float(matched) / float(centered_input.size())
\treturn radial_accuracy * angular_coverage * 100.0
'''
matcher_path.write_text(matcher[:start] + new_methods + matcher[end:], encoding="utf-8")
print("PATCHED: Shape Cutter matcher")
