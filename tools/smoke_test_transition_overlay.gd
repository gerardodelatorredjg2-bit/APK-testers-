extends SceneTree

func _cleanup_project_autoloads() -> void:
	for child in root.get_children():
		if is_instance_valid(child):
			child.queue_free()
	for _frame in 4:
		await process_frame
	ResourceLoader.clear_cache()

func _initialize() -> void:
	call_deferred("_run")

func _run() -> void:
	var overlay = root.get_node_or_null("GlobalOverlay")
	if overlay == null:
		push_error("TRANSITION_OVERLAY_SMOKE_FAILED: GlobalOverlay autoload missing")
		quit(1)
		return

	var completion_counter := [0]
	overlay.fade_finished.connect(func() -> void:
		completion_counter[0] += 1
	)

	overlay.fade_overlay(Color.BLACK, 0.25)
	await create_timer(0.04).timeout
	overlay.fade_overlay(Color(0.0, 0.0, 0.0, 0.0), 0.25)
	await create_timer(0.4).timeout

	if completion_counter[0] != 1:
		push_error("TRANSITION_OVERLAY_SMOKE_FAILED: expected one completion for overlapping fades, got " + str(completion_counter[0]))
		quit(1)
		return

	if overlay.control.mouse_filter != Control.MOUSE_FILTER_IGNORE:
		push_error("TRANSITION_OVERLAY_SMOKE_FAILED: overlay still blocks input after fade")
		quit(1)
		return

	print("TRANSITION_OVERLAY_SMOKE_PASS: overlapping fades complete once and release input")
	overlay = null
	await _cleanup_project_autoloads()
	quit(0)
