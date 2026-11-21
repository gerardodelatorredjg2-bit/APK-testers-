extends Node

const TEST_TIMEOUT_SECONDS := 25.0
const HOST_GRACE_SECONDS := 1.2

var role := ""
var address := ""
var finished := false
var passed := false
var result_message := ""

func _cleanup_project_autoloads() -> void:
	for child in get_tree().root.get_children():
		if child != self and is_instance_valid(child):
			child.queue_free()
	for _frame in 4:
		await get_tree().process_frame

func _ready() -> void:
	call_deferred("_run_test")

func _run_test() -> void:
	var args := OS.get_cmdline_user_args()
	if args.is_empty():
		_fail("Usage: -- host | -- client <ip:port>")
		return
	role = str(args[0])
	if role == "client":
		if args.size() < 2:
			_fail("Client mode requires host IP:PORT")
			return
		address = str(args[1])
	elif role != "host":
		_fail("Unknown LAN smoke role: " + role)
		return

	NetworkManager.active_backend_type = NetworkManager.NetworkBackendType.Lan
	NetworkManager.on_matchmaking_error.connect(_on_matchmaking_error)
	NetworkManager.join_refused.connect(_on_join_refused)
	if role == "host":
		NetworkManager.on_server_created.connect(_on_server_created)
		NetworkManager.on_peer_connected.connect(_on_host_peer_connected)
		NetworkManager.start_host("CI LAN Host")
	else:
		NetworkManager.join_allowed.connect(_on_client_join_allowed)
		NetworkManager.start_client("CI LAN Client", address)

	var started_at := Time.get_ticks_msec()
	while not finished and (Time.get_ticks_msec() - started_at) < int(TEST_TIMEOUT_SECONDS * 1000.0):
		await get_tree().create_timer(0.1).timeout

	if passed and role == "host":
		await get_tree().create_timer(HOST_GRACE_SECONDS).timeout

	if not passed:
		_fail("Timed out waiting for LAN handshake")
		return

	NetworkManager.leave_lobby()
	await get_tree().create_timer(0.15).timeout
	print("LAN_LOOPBACK_PASS:", role, ":", result_message)
	await _cleanup_project_autoloads()
	get_tree().quit(0)

func _on_server_created() -> void:
	var backend = NetworkManager.active_backend
	if backend == null or backend.direct_connect_address.is_empty():
		_on_matchmaking_error("Host did not publish a direct LAN address")
		return
	print("LAN_TEST_HOST_READY=", backend.direct_connect_address)

func _on_host_peer_connected(network_id: int) -> void:
	var backend = NetworkManager.active_backend
	if backend != null and backend.connected_players.size() >= 2 and network_id != 1:
		passed = true
		finished = true
		result_message = "Host accepted peer " + str(network_id)

func _on_client_join_allowed(_reconnect: bool, _game_in_progress: bool, _debug_only: bool) -> void:
	passed = true
	finished = true
	result_message = "Client completed player handshake"

func _on_join_refused(network_id: int, reason: int) -> void:
	_on_matchmaking_error("Join refused for peer %d (reason %d)" % [network_id, reason])

func _on_matchmaking_error(message: String) -> void:
	_fail(message)

func _fail(message: String) -> void:
	finished = true
	passed = false
	result_message = message
	push_error("LAN_LOOPBACK_FAILED: " + message)
	get_tree().quit(1)
