extends SceneTree

var scene_paths: Array = []
var failures: Array[String] = []

func _initialize() -> void:
	call_deferred("_run_scene_smoke_test")

func _collect_scenes(directory_path: String) -> void:
	var directory := DirAccess.open(directory_path)
	if directory == null:
		failures.append("Cannot open directory: " + directory_path)
		return
	directory.list_dir_begin()
	var entry := directory.get_next()
	while entry != "":
		if entry != "." and entry != "..":
			var full_path := directory_path.path_join(entry)
			if directory.current_is_dir():
				_collect_scenes(full_path)
			elif entry.get_extension().to_lower() == "tscn":
				scene_paths.append(full_path)
		entry = directory.get_next()
	directory.list_dir_end()

func _run_scene_smoke_test() -> void:
	_collect_scenes("res://minigames")
	scene_paths.sort()
	print("MINIGAME_SCENE_SMOKE_COUNT=", scene_paths.size())
	for scene_path in scene_paths:
		var packed_scene: Resource = ResourceLoader.load(
			scene_path, "PackedScene", ResourceLoader.CACHE_MODE_IGNORE
		)
		if packed_scene == null:
			failures.append(scene_path)
			push_error("MINIGAME_SCENE_LOAD_FAILED: " + scene_path)
		packed_scene = null
	if failures.is_empty():
		print("MINIGAME_SCENE_SMOKE_PASS: all ", scene_paths.size(), " scenes loaded")
		quit(0)
	else:
		push_error("MINIGAME_SCENE_SMOKE_FAILURES: " + str(failures.size()))
		for failure in failures:
			push_error(failure)
		quit(1)
