extends SceneTree

const TEST_TIMEOUT_SECONDS := 25.0
const HOST_GRACE_SECONDS := 1.2
const LAN_BACKEND_ENUM_VALUE := 4

var role := ""
var address := ""
var finished := false
var passed := false
var result_message := ""
var network_manager: Node

func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	if args.is_empty():
		push_error("Usage: -- host | -- client <ip:port>")
		quit(2)
		return
	role = str(args[0])
	if role == "client":
		if args.size() < 2:
			push_error("Client mode requires host IP:PORT")
			quit(2)
			return
		address = str(args[1])
	elif role != "host":
		push_error("Unknown LAN smoke role: " + role)
		quit(2)
		return
	call_deferred("_run_test")

func _run_test() -> void:
	await process_frame
	network_manager = root.get_node_or_null("NetworkManager")
	if network_manager == null:
		push_error("NetworkManager autoload is not present in this test process")
		quit(2)
		return

	# LAN is the fifth NetworkBackendType enum entry (Enet, Steam, Online, Relay, Lan).
	network_manager.set("active_backend_type", LAN_BACKEND_ENUM_VALUE)
	network_manager.connect("on_matchmaking_error", Callable(self, "_on_matchmaking_error"))
	network_manager.connect("join_refused", Callable(self, "_on_join_refused"))
	if role == "host":
		network_manager.connect("on_server_created", Callable(self, "_on_server_created"))
		network_manager.connect("on_peer_connected", Callable(self, "_on_host_peer_connected"))
		network_manager.call("start_host", "CI LAN Host")
	else:
		network_manager.connect("join_allowed", Callable(self, "_on_client_join_allowed"))
		network_manager.call("start_client", "CI LAN Client", address)

	var started_at := Time.get_ticks_msec()
	while not finished and (Time.get_ticks_msec() - started_at) < int(TEST_TIMEOUT_SECONDS * 1000.0):
		await create_timer(0.1).timeout

	if passed and role == "host":
		# Keep the server alive briefly so the client can receive its allow-join RPC.
		await create_timer(HOST_GRACE_SECONDS).timeout

	if not passed:
		result_message = "timed out waiting for LAN handshake"
		push_error("LAN_LOOPBACK_FAILED: " + result_message)

	if network_manager.get("active_backend") != null:
		network_manager.call("leave_lobby")

	if passed:
		print("LAN_LOOPBACK_PASS:", role, ":", result_message)
		quit(0)
	else:
		quit(1)

func _on_server_created() -> void:
	var backend: Node = network_manager.get("active_backend")
	if backend == null:
		_on_matchmaking_error("Host backend was not created")
		return
	var direct_address := str(backend.get("direct_connect_address"))
	if direct_address.is_empty():
		_on_matchmaking_error("Host did not publish a direct LAN address")
		return
	print("LAN_TEST_HOST_READY=", direct_address)

func _on_host_peer_connected(network_id: int) -> void:
	var backend: Node = network_manager.get("active_backend")
	if backend != null:
		var players: Dictionary = backend.get("connected_players")
		if players.size() >= 2 and network_id != 1:
			passed = true
			finished = true
			result_message = "host accepted peer " + str(network_id)

func _on_client_join_allowed(_reconnect: bool, _game_in_progress: bool, _debug_only: bool) -> void:
	passed = true
	finished = true
	result_message = "client completed player handshake"

func _on_join_refused(network_id: int, reason: int) -> void:
	_on_matchmaking_error("Join refused for peer %d (reason %d)" % [network_id, reason])

func _on_matchmaking_error(message: String) -> void:
	finished = true
	passed = false
	result_message = message
	push_error("LAN_LOOPBACK_ERROR: " + message)
