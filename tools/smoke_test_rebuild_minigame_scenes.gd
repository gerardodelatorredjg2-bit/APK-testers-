extends SceneTree

const MINIGAME_SCENES: Array[String] = [
	"res://minigames/chisel_gauntlet_multiplayer/chisel_gauntlet.tscn",
	"res://minigames/exploding_collar_race/exploding_collar_race.tscn",
	"res://minigames/escalator_pit/escalator_pit.tscn",
	"res://minigames/manufacture_gun/manufacture_gun.tscn",
	"res://minigames/smoke_break/smoke_break.tscn",
	"res://minigames/disco_dodge/disco_dodge.tscn",
	"res://minigames/shape_cutter/shape_cutter.tscn",
	"res://minigames/knife_at_the_office/knife_at_the_office.tscn",
	"res://minigames/scavenger_chairs/scavenger_chairs.tscn",
	"res://minigames/train_race/train_race.tscn",
	"res://minigames/duck_hunt/duck_hunt.tscn",
	"res://minigames/green_pea/green_pea.tscn",
	"res://minigames/dvd_roomba/dvd_roomba.tscn",
	"res://minigames/junk_platform/junk_platform.tscn",
	"res://minigames/spine_breaker/spine_breaker.tscn",
	"res://minigames/memorize_path/memorize_path.tscn",
	"res://minigames/forklift_certified/forklift_certified.tscn",
	"res://minigames/burn_recycle/burn_recycle.tscn",
	"res://minigames/cutscene_test/cutscene_test.tscn",
	"res://minigames/cutscene_game_02/cutscene_game_02.tscn",
]

func _initialize() -> void:
	call_deferred("_run")

func _run() -> void:
	var failures: Array[String] = []
	for path in MINIGAME_SCENES:
		var resource: Resource = load(path)
		if not resource is PackedScene:
			failures.append(path)
			push_error("MINIGAME_SCENE_LOAD_FAILED: " + path)
		else:
			print("MINIGAME_SCENE_LOAD_OK: " + path)
	if failures.is_empty():
		print("MINIGAME_SCENE_LOAD_PASS: %d scenes loaded." % MINIGAME_SCENES.size())
		quit(0)
	else:
		print("MINIGAME_SCENE_LOAD_FAILED: %d/%d" % [failures.size(), MINIGAME_SCENES.size()])
		quit(1)
