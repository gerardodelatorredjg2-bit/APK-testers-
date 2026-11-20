extends SceneTree

const MAIN_SCENE_PATH := "res://scenes/bootstrap/bootstrap.tscn"
const STARTUP_FRAMES := 180

func _cleanup_project_autoloads() -> void:
	for child in root.get_children():
		if is_instance_valid(child):
			child.queue_free()
	for _frame in 4:
		await process_frame

func _initialize() -> void:
	call_deferred("_run")

func _run() -> void:
	var packed := load(MAIN_SCENE_PATH) as PackedScene
	if packed == null:
		push_error("FULL_STARTUP_SMOKE_FAILED: main scene could not be loaded")
		quit(1)
		return

	var main_instance := packed.instantiate()
	if main_instance == null:
		push_error("FULL_STARTUP_SMOKE_FAILED: main scene could not be instantiated")
		quit(1)
		return

	root.add_child(main_instance)
	for _frame in STARTUP_FRAMES:
		await process_frame

	if not is_instance_valid(main_instance) or not main_instance.is_inside_tree():
		push_error("FULL_STARTUP_SMOKE_FAILED: main scene exited during startup")
		quit(1)
		return

	_stop_audio_nodes(root)
	main_instance.queue_free()
	main_instance = null
	packed = null
	await process_frame
	await process_frame
	print("FULL_STARTUP_SMOKE_PASS: main scene ran for ", STARTUP_FRAMES, " frames and shut down cleanly")
	await _cleanup_project_autoloads()
	quit(0)

func _stop_audio_nodes(node: Node) -> void:
	if node is AudioStreamPlayer or node is AudioStreamPlayer2D or node is AudioStreamPlayer3D:
		node.stop()
	for child in node.get_children():
		_stop_audio_nodes(child)
