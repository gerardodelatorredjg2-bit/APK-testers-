extends SceneTree

const PLAYER_SCENE := "res://minigames/exploding_collar_race/components/player/exploding_collar_race_player.tscn"
const PLAYER_ROOT := "Visuals/main character2"
const TORSO_TREE := PLAYER_ROOT + "/TorsoAnimationTree"
const TORSO_ANIM := PLAYER_ROOT + "/anim_character rig torso cut off"
const TORSO_SKELETON := PLAYER_ROOT + "/Armature torso cut off/character torso cut off4/Armature/Skeleton3D"

func _cleanup_project_autoloads() -> void:
	for child in root.get_children():
		if is_instance_valid(child):
			child.queue_free()
	for _frame in 4:
		await process_frame

func _initialize() -> void:
	call_deferred("_run")

func _fail(message: String) -> void:
	push_error("CRAWL_ANIMATION_SMOKE_FAILED: " + message)
	quit(1)

func _run() -> void:
	var packed := load(PLAYER_SCENE) as PackedScene
	if packed == null:
		_fail("player scene failed to load")
		return

	var player := packed.instantiate()
	root.add_child(player)
	player.set_physics_process(false)
	await process_frame

	var tree := player.get_node_or_null(TORSO_TREE) as AnimationTree
	var animation_player := player.get_node_or_null(TORSO_ANIM) as AnimationPlayer
	var skeleton := player.get_node_or_null(TORSO_SKELETON) as Skeleton3D
	if tree == null or animation_player == null or skeleton == null:
		_fail("torso animation tree, player or skeleton node is missing")
		return

	if not tree.active:
		_fail("torso AnimationTree is inactive")
		return

	var crawl := animation_player.get_animation("crawl_overwrite")
	var crawl_idle := animation_player.get_animation("crawl_idle_overwrite")
	if crawl == null or crawl_idle == null:
		_fail("crawl animation clips are not available in the torso AnimationPlayer")
		return

	var resolved_bone_tracks := 0
	for track_index in crawl.get_track_count():
		var track_path := String(crawl.track_get_path(track_index))
		if not track_path.contains(":"):
			continue
		var bone_name := track_path.get_slice(":", 1)
		if skeleton.find_bone(bone_name) >= 0:
			resolved_bone_tracks += 1
		else:
			_fail("crawl animation references missing torso bone: " + bone_name)
			return

	if resolved_bone_tracks < 8:
		_fail("too few crawl tracks resolve to the torso skeleton: " + str(resolved_bone_tracks))
		return

	var initial_poses: Array[Quaternion] = []
	for bone_index in skeleton.get_bone_count():
		initial_poses.append(skeleton.get_bone_pose_rotation(bone_index))

	tree.set("parameters/move_blend/blend_amount", 1.0)
	for frame_index in 36:
		await process_frame

	var animated_bones := 0
	for bone_index in skeleton.get_bone_count():
		var later_pose := skeleton.get_bone_pose_rotation(bone_index)
		if not initial_poses[bone_index].is_equal_approx(later_pose):
			animated_bones += 1

	if animated_bones == 0:
		_fail("crawl clip resolved but no torso bone pose changed during playback")
		return

	print("CRAWL_ANIMATION_SMOKE_PASS: ", resolved_bone_tracks, " bone tracks resolved; ", animated_bones, " bones moved")
	player.queue_free()
	player = null
	packed = null
	tree = null
	animation_player = null
	skeleton = null
	await _cleanup_project_autoloads()
	quit(0)
