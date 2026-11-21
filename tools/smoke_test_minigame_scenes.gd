extends SceneTree

var scene_paths: Array = []
var failures: Array[String] = []

const CORE_GAME_SCENES: Array[String] = [
	"res://scenes/bootstrap/bootstrap.tscn",
	"res://scenes/main_menu/main_menu.tscn",
	"res://scenes/lobby/lobby_scene.tscn",
	"res://scenes/local_lobby/local_lobby.tscn",
	"res://scenes/local_game/local_game.tscn",
	"res://scenes/game.tscn",
	"res://scenes/character_customization.tscn",
	"res://scenes/credits/credits_scene.tscn",
	"res://scenes/debug_lobby/debug_lobby.tscn",
]

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

func _cleanup_project_autoloads() -> void:
	for child in root.get_children():
		if is_instance_valid(child):
			child.queue_free()
	for _frame in 4:
		await process_frame
	ResourceLoader.clear_cache()

func _run_scene_smoke_test() -> void:
	_collect_scenes("res://minigames")
	for core_scene in CORE_GAME_SCENES:
		if ResourceLoader.exists(core_scene):
			scene_paths.append(core_scene)
		else:
			failures.append(core_scene)
			push_error("CORE_SCENE_MISSING: " + core_scene)
	scene_paths.sort()
	print("MINIGAME_SCENE_SMOKE_COUNT=", scene_paths.size())
	var processed_scenes := 0
	for scene_path in scene_paths:
		var packed_scene: Resource = ResourceLoader.load(
			scene_path, "PackedScene", ResourceLoader.CACHE_MODE_IGNORE_DEEP
		)
		if packed_scene == null:
			failures.append(scene_path)
			push_error("MINIGAME_SCENE_LOAD_FAILED: " + scene_path)
		packed_scene = null
		processed_scenes += 1
		await process_frame
	await process_frame
	await process_frame
	if failures.is_empty():
		print("MINIGAME_SCENE_SMOKE_PASS: all ", scene_paths.size(), " scenes loaded")
		await _cleanup_project_autoloads()
		quit(0)
	else:
		push_error("MINIGAME_SCENE_SMOKE_FAILURES: " + str(failures.size()))
		for failure in failures:
			push_error(failure)
		await _cleanup_project_autoloads()
		quit(1)
