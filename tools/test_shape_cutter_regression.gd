extends SceneTree

const MatcherScript = preload("res://minigames/shape_cutter/scripts/pattern_matcher.gd")

func _initialize() -> void:
	call_deferred("_run")

func _fail(message: String) -> void:
	push_error("SHAPE_CUTTER_REGRESSION_FAILED: " + message)
	quit(1)

func _run() -> void:
	var matcher = MatcherScript.new()
	var rectangle: Array[Vector2] = []
	var offset := Vector2(3.25, -2.75)
	for i in range(16):
		var t := float(i) / 16.0
		rectangle.append(Vector2(lerp(-0.4, 0.4, t), -0.4) + offset)
	for i in range(16):
		var t := float(i) / 16.0
		rectangle.append(Vector2(0.4, lerp(-0.4, 0.4, t)) + offset)
	for i in range(16):
		var t := float(i) / 16.0
		rectangle.append(Vector2(lerp(0.4, -0.4, t), 0.4) + offset)
	for i in range(16):
		var t := float(i) / 16.0
		rectangle.append(Vector2(-0.4, lerp(0.4, -0.4, t)) + offset)
	var rectangle_score: float = matcher.check_against_pattern(rectangle, MatcherScript.Pattern.Rectangle)
	if rectangle_score < 90.0:
		_fail("translated rectangle scored too low: " + str(rectangle_score))
		return

	var circle: Array[Vector2] = []
	for i in range(64):
		var angle := TAU * float(i) / 64.0
		circle.append(Vector2(sin(angle), cos(angle)) * 0.4 + offset)
	var circle_score: float = matcher.check_against_pattern(circle, MatcherScript.Pattern.Circle)
	if circle_score < 90.0:
		_fail("full circle scored too low: " + str(circle_score))
		return

	var arc: Array[Vector2] = []
	for i in range(17):
		var angle := deg_to_rad(170.0 + (20.0 * float(i) / 16.0))
		arc.append(Vector2(cos(angle), sin(angle)) * 0.4 + offset)
	var arc_score: float = matcher.check_against_pattern(arc, MatcherScript.Pattern.Circle)
	if arc_score > 20.0:
		_fail("short arc across angular seam scored too high: " + str(arc_score))
		return

	print("SHAPE_CUTTER_REGRESSION_PASS: translated rectangle, offset full circle, and seam-crossing arc")
	quit(0)
