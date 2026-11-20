extends SceneTree

const ANIMATION_FILES := [
	{
		"source": "res://animations/crawl_idle_overwrite.res",
		"output": "res://animations/crawl_idle_overwrite_mobile.tres",
	},
	{
		"source": "res://animations/crawl_overwrite.res",
		"output": "res://animations/crawl_overwrite_mobile.tres",
	},
]
const PLAYER_SCENE := "res://minigames/exploding_collar_race/components/player/exploding_collar_race_player.tscn"
const SKELETON_PATH := "Visuals/main character2/Armature torso cut off/character torso cut off4/Armature/Skeleton3D"

func _initialize() -> void:
	call_deferred("_repair")

func _repair() -> void:
	var packed := load(PLAYER_SCENE) as PackedScene
	if packed == null:
		push_error("CRAWL_REPAIR_FAILED: cannot load player scene")
		quit(1)
		return
	var player := packed.instantiate()
	root.add_child(player)
	await process_frame
	var skeleton := player.get_node_or_null(SKELETON_PATH) as Skeleton3D
	if skeleton == null:
		push_error("CRAWL_REPAIR_FAILED: torso skeleton missing")
		quit(1)
		return

	var total_repaired := 0
	for item in ANIMATION_FILES:
		var animation := load(item.source) as Animation
		if animation == null:
			push_error("CRAWL_REPAIR_FAILED: missing animation " + item.source)
			quit(1)
			return

		var copy := animation.duplicate(true) as Animation
		var fixed_in_animation := 0
		for track_index in copy.get_track_count():
			var old_path := str(copy.track_get_path(track_index))
			var separator := old_path.rfind(":")
			if separator < 0:
				continue
			var prefix := old_path.substr(0, separator + 1)
			var bone_name := old_path.substr(separator + 1)
			if skeleton.find_bone(bone_name) >= 0:
				continue

			var aliases := [
				[bone_name.replace("_L", ".L"), bone_name.replace("_R", ".R")],
				[bone_name.replace("_l", ".L"), bone_name.replace("_r", ".R")],
			]
			var corrected := ""
			for alias_pair in aliases:
				for candidate in alias_pair:
					if candidate != bone_name and skeleton.find_bone(candidate) >= 0:
						corrected = candidate
						break
				if not corrected.is_empty():
					break

			if corrected.is_empty():
				push_error("CRAWL_REPAIR_FAILED: no matching torso bone for " + old_path)
				quit(1)
				return

			copy.track_set_path(track_index, NodePath(prefix + corrected))
			fixed_in_animation += 1

		if fixed_in_animation == 0:
			print("CRAWL_REPAIR_INFO: no bone aliases needed in ", item.source)
		else:
			print("CRAWL_REPAIR: remapped ", fixed_in_animation, " tracks in ", item.source)
		if ResourceSaver.save(copy, item.output) != OK:
			push_error("CRAWL_REPAIR_FAILED: could not save " + item.output)
			quit(1)
			return
		total_repaired += fixed_in_animation

	var scene_text := FileAccess.get_file_as_string(PLAYER_SCENE)
	if scene_text.is_empty():
		push_error("CRAWL_REPAIR_FAILED: cannot read player scene")
		quit(1)
		return
	var uid_regex := RegEx.new()
	uid_regex.compile(' uid="uid://[^"]+"')
	var lines := scene_text.split("\n")
	var replaced := 0
	for index in lines.size():
		if lines[index].contains('id="25"') and lines[index].contains("crawl_idle_overwrite.res"):
			lines[index] = uid_regex.sub(lines[index], "", true)
			lines[index] = lines[index].replace("crawl_idle_overwrite.res", "crawl_idle_overwrite_mobile.tres")
			replaced += 1
		elif lines[index].contains('id="26"') and lines[index].contains("crawl_overwrite.res"):
			lines[index] = uid_regex.sub(lines[index], "", true)
			lines[index] = lines[index].replace("crawl_overwrite.res", "crawl_overwrite_mobile.tres")
			replaced += 1
	if replaced != 2:
		push_error("CRAWL_REPAIR_FAILED: expected 2 crawl resource refs, replaced " + str(replaced))
		quit(1)
		return
	if not _write_text(PLAYER_SCENE, "\n".join(lines)):
		push_error("CRAWL_REPAIR_FAILED: could not save player scene references")
		quit(1)
		return

	print("CRAWL_REPAIR_PASS: repaired ", total_repaired, " mismatched bone tracks and rewired both crawl clips")
	player.queue_free()
	quit(0)

func _write_text(path: String, contents: String) -> bool:
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file == null:
		return false
	file.store_string(contents)
	file.close()
	return true
