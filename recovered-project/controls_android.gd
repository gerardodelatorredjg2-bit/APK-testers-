extends Node2D

var controls: Dictionary = {}

func _ready() -> void:
	_collect_controls()
	if Globals.touch_controls_defaults.is_empty():
		Globals.touch_controls_defaults = capture_layout()
	apply_layout(Globals.settings.get("touch_controls", {}))

func _collect_controls() -> void:
	controls.clear()
	for control in $CanvasLayer.get_children():
		if control is TouchScreenButton and control.texture_normal != null:
			controls[control.name] = control
		elif control is VirtualJoystick:
			controls[control.name] = control

func _input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.pressed and _is_point_inside_any_button(event.position):
		get_viewport().set_input_as_handled()

func _is_point_inside_any_button(point: Vector2) -> bool:
	for control in controls.values():
		if control is TouchScreenButton and control.is_visible_in_tree() and control.texture_normal:
			var size: Vector2 = control.texture_normal.get_size() * Vector2(abs(control.scale.x), abs(control.scale.y))
			var rect := Rect2(control.global_position, size).abs()
			if rect.has_point(point):
				return true
	return false

func get_controls() -> Dictionary:
	return controls

func set_editing(editing: bool) -> void:
	for control in controls.values():
		control.process_mode = Node.PROCESS_MODE_DISABLED if editing else Node.PROCESS_MODE_INHERIT

func capture_layout() -> Dictionary:
	var layout: Dictionary = {}
	for control_name in controls:
		var control = controls[control_name]
		layout[control_name] = {
			"pos": [control.global_position.x, control.global_position.y],
			"scale": [control.scale.x, control.scale.y],
			"opacity": control.modulate.a
		}
	return layout

func apply_layout(layout: Dictionary) -> void:
	for control_name in layout:
		if not controls.has(control_name):
			continue
		var control = controls[control_name]
		var data = layout[control_name]
		if not data is Dictionary:
			continue
		var position_data = data.get("pos", [])
		var scale_data = data.get("scale", [])
		if position_data is Array and position_data.size() >= 2 and scale_data is Array and scale_data.size() >= 2:
			var px = float(position_data[0])
			var py = float(position_data[1])
			var sx = float(scale_data[0])
			var sy = float(scale_data[1])
			if is_finite(px) and is_finite(py) and is_finite(sx) and is_finite(sy):
				control.global_position = Vector2(px, py)
				control.scale = Vector2(clampf(sx, 0.25, 4.0), clampf(sy, 0.25, 4.0))
				control.modulate.a = clampf(float(data.get("opacity", 1.0)), 0.0, 1.0)

func apply_default_layout() -> void:
	apply_layout(Globals.touch_controls_defaults)

func save_layout() -> void:
	Globals.settings["touch_controls"] = capture_layout()
	Globals.save_current_settings()
