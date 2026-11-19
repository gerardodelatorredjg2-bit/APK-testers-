from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()

def patch(path: str, old: str, new: str, expected: int = 1) -> None:
    p = root / path
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{path}: expected {expected} occurrence(s), found {count}")
    p.write_text(text.replace(old, new), encoding="utf-8")

# A LAN backend reuses Machine Party's existing player handshake and RPCs,
# but deliberately skips diagnostics, punch-through, UPnP and Matchmaker calls.
backend = r'''extends "res://modules/multiplayer/backends/online_enet_backend.gd"

class_name LanEnetBackend

const DEFAULT_LAN_PORT: int = 25565

func _ready() -> void:
	reconnect_check_property = "name"
	multiplayer.peer_connected.connect(_on_player_connected)
	multiplayer.peer_disconnected.connect(_on_player_disconnected)
	multiplayer.server_disconnected.connect(_on_host_disconnected)
	multiplayer.connected_to_server.connect(_on_connected_to_server)
	multiplayer.connection_failed.connect(_on_connection_failed)

func join_host(player_name: String) -> void:
	multiplayer_peer = ENetMultiplayerPeer.new()
	host_port = DEFAULT_LAN_PORT
	var max_clients: int = max(1, NetworkManager.max_player_count - 1)
	var server_error: Error = multiplayer_peer.create_server(host_port, max_clients)

	if server_error != OK:
		multiplayer_peer = ENetMultiplayerPeer.new()
		host_port = _get_free_port()
		if host_port > 0:
			server_error = multiplayer_peer.create_server(host_port, max_clients)

	if server_error != OK:
		on_matchmaking_error.emit("Could not start local host (%d)" % server_error)
		return

	multiplayer.multiplayer_peer = multiplayer_peer
	if player_name.strip_edges().is_empty():
		player_name = "Player 1"

	save_player_info(player_name, true)
	joinable = true
	hosting = true
	host_ip = _get_local_ip()
	if host_ip.is_empty():
		multiplayer_peer.close()
		hosting = false
		joinable = false
		on_matchmaking_error.emit("No local Wi-Fi or hotspot IPv4 address was found. Enable the hotspot and try again.")
		return
	direct_connect_address = "%s:%d" % [host_ip, host_port]
	print("Machine Party LAN host: %s" % direct_connect_address)
	on_server_created.emit()

func join_client_direct(player_name: String, address: String) -> void:
	var cleaned: String = address.strip_edges()
	var parts: PackedStringArray = cleaned.split(":")
	if parts.size() != 2 or not parts[0].is_valid_ip_address() or not parts[1].is_valid_int():
		join_refused.emit(0, NetworkManager.RefuseReason.None)
		return

	var port: int = parts[1].to_int()
	if port < 1 or port > 65535:
		join_refused.emit(0, NetworkManager.RefuseReason.None)
		return

	multiplayer_peer = ENetMultiplayerPeer.new()
	var client_error: Error = multiplayer_peer.create_client(parts[0], port)
	if client_error != OK:
		join_refused.emit(0, NetworkManager.RefuseReason.None)
		return

	host_ip = parts[0]
	host_port = port
	direct_connect_address = cleaned
	multiplayer.multiplayer_peer = multiplayer_peer
	if player_name.strip_edges().is_empty():
		player_name = "Player " + str(multiplayer.get_unique_id())
	save_player_info(player_name)
	on_client_created.emit()

func join_client(player_name: String, room_code: String = "") -> void:
	join_client_direct(player_name, room_code)
'''
backend_path = root / "modules/multiplayer/backends/lan_enet_backend.gd"
backend_path.parent.mkdir(parents=True, exist_ok=True)
backend_path.write_text(backend, encoding="utf-8")

patch(
    "modules/multiplayer/network_manager.gd",
    "enum NetworkBackendType{\n\tEnet, Steam, Online, Relay\n}",
    "enum NetworkBackendType{\n\tEnet, Steam, Online, Relay, Lan\n}",
)
patch(
    "modules/multiplayer/network_manager.gd",
    "var active_backend_type: NetworkBackendType = NetworkBackendType.Relay",
    "var active_backend_type: NetworkBackendType = NetworkBackendType.Lan",
)
patch(
    "modules/multiplayer/network_manager.gd",
    "active_backend_type = NetworkBackendType.Relay\n\t\tcreate_backend()",
    "active_backend_type = NetworkBackendType.Lan\n\t\tcreate_backend()",
)
patch(
    "modules/multiplayer/network_manager.gd",
    "\t\tNetworkBackendType.Steam:\n",
    '\t\tNetworkBackendType.Lan:\n\n\t\t\tset_active_backend(preload("res://modules/multiplayer/backends/lan_enet_backend.gd"))\n\n\t\tNetworkBackendType.Steam:\n',
)
patch(
    "modules/multiplayer/network_manager.gd",
    "\treturn parts.size() == 2 and parts[0].is_valid_ip_address() and parts[1].is_valid_int()\n\nfunc backend_type_for_join_code(code: String) -> NetworkBackendType:\n\tvar cleaned: String = code.strip_edges()\n\tif cleaned.is_valid_int() and cleaned.length() == 18 and Steamworks.is_initialized:\n\t\treturn NetworkBackendType.Steam\n\treturn NetworkBackendType.Relay",
    "\tif parts.size() != 2 or not parts[0].is_valid_ip_address() or not parts[1].is_valid_int():\n\t\treturn false\n\tvar port: int = parts[1].to_int()\n\treturn port >= 1 and port <= 65535\n\nfunc backend_type_for_join_code(code: String) -> NetworkBackendType:\n\tvar cleaned: String = code.strip_edges()\n\tif _is_direct_address(cleaned):\n\t\treturn NetworkBackendType.Lan\n\tif cleaned.is_valid_int() and cleaned.length() == 18 and Steamworks.is_initialized:\n\t\treturn NetworkBackendType.Steam\n\treturn NetworkBackendType.Lan",
)

patch(
    "scenes/lobby/scripts/lobby_scene.gd",
    "NetworkManager.active_backend_type = NetworkManager.NetworkBackendType.Relay",
    "NetworkManager.active_backend_type = NetworkManager.NetworkBackendType.Lan",
)

# Keep clipboard quick-join, while offering a local IP:PORT dialog when there
# is no usable code. This removes the need for both Android devices to share a clipboard.
patch(
    "scenes/main_menu/scripts/main_menu.gd",
    '''\tstart_join_button.pressed.connect( func():

\t\tvar clipboard = DisplayServer.clipboard_get().strip_edges().to_upper()
\t\tvar is_direct: bool = clipboard.count(":") == 1
\t\tif is_direct:
\t\t\tvar parts: PackedStringArray = clipboard.split(":")
\t\t\tif not (parts.size() == 2 and parts[0].is_valid_ip_address() and parts[1].is_valid_int()):
\t\t\t\tis_direct = false

\t\tvar is_steam_code: bool = Steamworks.is_initialized and clipboard.is_valid_int() and clipboard.length() == 18

\t\tif not is_direct and not is_steam_code and clipboard.length() != 6:
\t\t\tset_tooltip(tr("LOC_TAG_NO_ROOM_ID"), Color.CRIMSON)
\t\t\treturn

\t\tGameManager.join_lobby_id = clipboard
\t\tGameManager.host_game = false
\t\tGameManager.custom_game = false

\t\tswitch_to_lobby_scene()

\t)''',
    '''\tstart_join_button.pressed.connect( func():

\t\tvar clipboard: String = DisplayServer.clipboard_get().strip_edges()
\t\tvar is_direct: bool = NetworkManager._is_direct_address(clipboard)
\t\tvar is_steam_code: bool = Steamworks.is_initialized and clipboard.is_valid_int() and clipboard.length() == 18

\t\tif not is_direct and not is_steam_code:
\t\t\t_show_lan_join_dialog(clipboard)
\t\t\treturn

\t\t_join_with_address_or_code(clipboard)

\t)''',
)

insert_after = "func switch_to_lobby_scene():\n"
helpers = r'''func _join_with_address_or_code(value: String) -> void:
	GameManager.join_lobby_id = value.strip_edges()
	GameManager.host_game = false
	GameManager.custom_game = false
	switch_to_lobby_scene()

func _show_lan_join_dialog(prefill: String = "") -> void:
	var popup: PopupPanel = PopupPanel.new()
	popup.name = "LANJoinPopup"
	popup.size = Vector2i(560, 270)
	popup.transient = true
	popup.exclusive = true
	panel.theme = theme

	var panel: PanelContainer = PanelContainer.new()
	panel.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	popup.add_child(panel)

	var margin: MarginContainer = MarginContainer.new()
	margin.add_theme_constant_override("margin_left", 28)
	margin.add_theme_constant_override("margin_right", 28)
	margin.add_theme_constant_override("margin_top", 24)
	margin.add_theme_constant_override("margin_bottom", 24)
	panel.add_child(margin)

	var layout: VBoxContainer = VBoxContainer.new()
	layout.alignment = BoxContainer.ALIGNMENT_CENTER
	layout.add_theme_constant_override("separation", 14)
	margin.add_child(layout)

	var title: Label = Label.new()
	title.text = "JOIN LOCAL PARTY"
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	layout.add_child(title)

	var hint: Label = Label.new()
	hint.text = "Enter the host's IP:PORT shown on their lobby screen."
	hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	layout.add_child(hint)

	var address_input: LineEdit = LineEdit.new()
	address_input.placeholder_text = "192.168.43.1:25565"
	address_input.text = prefill
	address_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	address_input.custom_minimum_size = Vector2(0, 48)
	address_input.alignment = HORIZONTAL_ALIGNMENT_CENTER
	layout.add_child(address_input)

	var buttons: HBoxContainer = HBoxContainer.new()
	buttons.alignment = BoxContainer.ALIGNMENT_CENTER
	buttons.add_theme_constant_override("separation", 18)
	layout.add_child(buttons)

	var cancel_button: Button = Button.new()
	cancel_button.text = "BACK"
	cancel_button.custom_minimum_size = Vector2(130, 44)
	buttons.add_child(cancel_button)

	var join_button: Button = Button.new()
	join_button.text = "JOIN"
	join_button.custom_minimum_size = Vector2(130, 44)
	buttons.add_child(join_button)

	add_child(popup)
	cancel_button.pressed.connect(func(): popup.queue_free())
	join_button.pressed.connect(func():
		var address: String = address_input.text.strip_edges()
		if not NetworkManager._is_direct_address(address):
			hint.text = "Invalid address. Enter an IPv4 address and port, e.g. 192.168.43.1:25565."
			hint.add_theme_color_override("font_color", Color.CRIMSON)
			address_input.grab_focus()
			return
		popup.queue_free()
		_join_with_address_or_code(address)
	)
	address_input.text_submitted.connect(func(_text: String): join_button.pressed.emit())
	popup.popup_centered()
	address_input.call_deferred("grab_focus")

'''
if insert_after not in (root / "scenes/main_menu/scripts/main_menu.gd").read_text(encoding="utf-8"):
    raise SystemExit("main menu insertion point not found")
patch("scenes/main_menu/scripts/main_menu.gd", insert_after, helpers + insert_after)

patch(
    "scenes/lobby/scripts/lobby_scene.gd",
    "NetworkManager.join_refused.connect(_on_join_refused)\n",
    "NetworkManager.join_refused.connect(_on_join_refused)\n\tNetworkManager.on_matchmaking_error.connect(_on_matchmaking_error)\n",
)
match_error_method = """func _on_matchmaking_error(message: String):

\tloading = false
\tload_timeout_timer.stop()

\tif multiplayer:
\t\tNetworkManager.leave_lobby()
\t\tNetworkManager.cancel(multiplayer.get_unique_id())

\tGlobalOverlay.fade_overlay(Color.BLACK, 0.5)
\tset_ambience(false)
\tawait GlobalOverlay.fade_finished

\tGameManager.main_menu_dialog_message = message
\tif not is_inside_tree():
\t\treturn
\tget_tree().change_scene_to_file("res://scenes/main_menu/main_menu.tscn")

"""
patch(
    "scenes/lobby/scripts/lobby_scene.gd",
    "func _on_load_timeout():",
    match_error_method + "func _on_load_timeout():",
)

# The original Android package marks Vulkan as mandatory. The Compatibility
# renderer uses OpenGL ES 3.0 and supports older Android GPUs without Vulkan.
patch(
    "project.godot",
    'config/features=PackedStringArray("4.5", "Forward Plus")',
    'config/features=PackedStringArray("4.5", "GL Compatibility")',
)
patch(
    "project.godot",
    'renderer/rendering_method="mobile"',
    'renderer/rendering_method="gl_compatibility"',
)

# The recovered project keeps the Steam autoload for its desktop build, but the
# Android LAN export deliberately omits the Steam GDExtension. Guard lookup so
# the app can start without Steam while preserving Steam behavior where present.
patch(
    "modules/steamworks/steamworks.gd",
    'steam = Engine.get_singleton("Steam")',
    'steam = Engine.get_singleton("Steam") if Engine.has_singleton("Steam") else null',
)
patch(
    "autoloads/game_manager.gd",
    'var steam: = Engine.get_singleton("Steam")',
    'var steam = Engine.get_singleton("Steam") if Engine.has_singleton("Steam") else null',
)

# Godot has a separate mobile renderer override. Changing only the base
# rendering method leaves Android on the default Mobile/Vulkan renderer.
project_file = root / "project.godot"
project_lines = project_file.read_text(encoding="utf-8").splitlines()
mobile_key = "renderer/rendering_method.mobile"
mobile_setting = 'renderer/rendering_method.mobile="gl_compatibility"'
rendering_index = next((i for i, line in enumerate(project_lines) if line.strip() == "[rendering]"), -1)
if rendering_index < 0:
    project_lines.extend(["", "[rendering]", mobile_setting])
else:
    existing_index = next(
        (i for i, line in enumerate(project_lines[rendering_index + 1:], rendering_index + 1)
         if line.strip().startswith(mobile_key + "=")),
        -1,
    )
    if existing_index >= 0:
        project_lines[existing_index] = mobile_setting
    else:
        project_lines.insert(rendering_index + 1, mobile_setting)

# Android export requires mobile ETC2/ASTC import variants. This is a project
# setting (not an Android preset option), and the import cache must be rebuilt
# after enabling it so .etc2.ctex files are actually generated.
etc2_key = "textures/vram_compression/import_etc2_astc"
etc2_setting = etc2_key + "=true"
etc2_index = next(
    (i for i, line in enumerate(project_lines[rendering_index + 1:], rendering_index + 1)
     if line.strip().startswith(etc2_key + "=")),
    -1,
)
if etc2_index >= 0:
    project_lines[etc2_index] = etc2_setting
else:
    project_lines.insert(rendering_index + 1, etc2_setting)
project_file.write_text("\n".join(project_lines) + "\n", encoding="utf-8")

# Repair an unrelated but real script-parse failure in the isolated Shape Cutter
# test scene; keep its intended PatternMatcher API spelling.
patch(
    "minigames/shape_cutter/scripts/shape_cutter_test.gd",
    "PatternMatcher.pattern_points",
    "PatternMatcher.PatternPoints",
)

# The original project uses a custom AudioEffectGate GDExtension that is not
# available in the recovered Android package's standard desktop editor toolchain.
# Voice gating is non-essential to LAN gameplay; remove only those two bus effects
# so the stock Godot editor can import the recovered project.
patch(
    "bus_layout.tres",
    '[sub_resource type="AudioEffectGate" id="AudioEffectGate_j3pel"]\nresource_name = "Gate"\n\n',
    "",
)
patch(
    "bus_layout.tres",
    '[sub_resource type="AudioEffectGate" id="AudioEffectGate_g28q7"]\nresource_name = "Gate"\n\n',
    "",
)
patch(
    "bus_layout.tres",
    'bus/6/effect/1/effect = SubResource("AudioEffectGate_j3pel")\nbus/6/effect/1/enabled = true\n',
    "",
)
patch(
    "bus_layout.tres",
    'bus/7/effect/0/effect = SubResource("AudioEffectGate_g28q7")\nbus/7/effect/0/enabled = true\n',
    "",
)

# Keep resource load-step counts consistent after removing the two custom effects.
patch(
    "bus_layout.tres",
    'load_steps=6 format=3',
    'load_steps=4 format=3',
)
patch(
    "default_bus_layout.tres",
    'load_steps=5 format=3',
    'load_steps=3 format=3',
)

# The decompiled Steam join path passes an integer even though the manager
# and backend signatures consistently accept the lobby ID as a String.
patch(
    "modules/multiplayer_lobby/multiplayer_menu.gd",
    "NetworkManager.start_client(debug_lobby_handler.name_entry.text, int(clipboard))",
    "NetworkManager.start_client(debug_lobby_handler.name_entry.text, clipboard)",
)

# The fallback bus layout also contains the unavailable custom gate effect.
for bus_file in ("default_bus_layout.tres",):
    patch(
        bus_file,
        '[sub_resource type="AudioEffectGate" id="AudioEffectGate_j3pel"]\nresource_name = "Gate"\n\n',
        "",
    )
    patch(
        bus_file,
        '[sub_resource type="AudioEffectGate" id="AudioEffectGate_g28q7"]\nresource_name = "Gate"\n\n',
        "",
    )
    patch(
        bus_file,
        'bus/6/effect/1/effect = SubResource("AudioEffectGate_j3pel")\nbus/6/effect/1/enabled = true\n',
        "",
    )
    patch(
        bus_file,
        'bus/7/effect/0/effect = SubResource("AudioEffectGate_g28q7")\nbus/7/effect/0/enabled = true\n',
        "",
    )

# Recovered scenes do not always include Godot's UID cache. Use the stable
# res:// path for this scene preload so a clean import can resolve it.
patch(
    "minigames/intermission_new/components/intermission_briefing_screen.gd",
    'preload("uid://bwixccr50fbc2")',
    'preload("res://modules/user_interface/intermission_control_input_label/intermission_control_input_label.tscn")',
)



# LAN discovery: UDP broadcast on the same Wi-Fi/hotspot, with manual IP:PORT fallback.
discovery = r'''extends Node
class_name LanDiscovery

const DISCOVERY_PORT: int = 25566
const DISCOVER_PACKET: String = "MACHINE_PARTY_LAN_DISCOVER_V1"
const HOST_PREFIX: String = "MACHINE_PARTY_LAN_HOST_V1|"

var _host_socket: PacketPeerUDP
var _host_port: int = 0
var _host_name: String = ""

func start_host(game_port: int, player_name: String) -> Error:
	stop_host()
	_host_socket = PacketPeerUDP.new()
	var err: Error = _host_socket.bind(DISCOVERY_PORT, "*")
	if err != OK:
		_host_socket = null
		return err
	_host_socket.set_broadcast_enabled(true)
	_host_port = game_port
	_host_name = player_name
	set_process(true)
	return OK

func stop_host() -> void:
	set_process(false)
	if _host_socket:
		_host_socket.close()
	_host_socket = null

func _process(_delta: float) -> void:
	if not _host_socket:
		return
	while _host_socket.get_available_packet_count() > 0:
		var packet: String = _host_socket.get_packet().get_string_from_utf8().strip_edges()
		if packet != DISCOVER_PACKET:
			continue
		var sender_ip: String = _host_socket.get_packet_ip()
		var sender_port: int = _host_socket.get_packet_port()
		var reply: String = HOST_PREFIX + JSON.stringify({
			"name": _host_name,
			"port": _host_port
		})
		_host_socket.set_dest_address(sender_ip, sender_port)
		_host_socket.put_packet(reply.to_utf8_buffer())

func discover_hosts(timeout_seconds: float = 2.0) -> Array[Dictionary]:
	var socket := PacketPeerUDP.new()
	socket.set_broadcast_enabled(true)
	var bind_error: Error = socket.bind(0, "*")
	if bind_error != OK:
		return []
	socket.set_dest_address("255.255.255.255", DISCOVERY_PORT)
	socket.put_packet(DISCOVER_PACKET.to_utf8_buffer())
	var found: Array[Dictionary] = []
	var seen: Dictionary = {}
	var deadline: int = Time.get_ticks_msec() + int(timeout_seconds * 1000.0)
	while Time.get_ticks_msec() < deadline:
		while socket.get_available_packet_count() > 0:
			var response: String = socket.get_packet().get_string_from_utf8()
			if not response.begins_with(HOST_PREFIX):
				continue
			var parsed: Variant = JSON.parse_string(response.substr(HOST_PREFIX.length()))
			if not parsed is Dictionary:
				continue
			var ip: String = socket.get_packet_ip()
			var port: int = int(parsed.get("port", 0))
			if ip.is_empty() or port < 1 or port > 65535:
				continue
			var address: String = "%s:%d" % [ip, port]
			if seen.has(address):
				continue
			seen[address] = true
			found.append({"name": str(parsed.get("name", "Machine Party")), "address": address})
		await get_tree().process_frame
	socket.close()
	return found

func _exit_tree() -> void:
	stop_host()
'''
(root / "modules/multiplayer/backends/lan_discovery.gd").write_text(discovery, encoding="utf-8")

# Start the UDP discovery responder alongside the LAN ENet host.
backend_file = root / "modules/multiplayer/backends/lan_enet_backend.gd"
backend_text = backend_file.read_text(encoding="utf-8")
backend_text = backend_text.replace(
	'const DEFAULT_LAN_PORT: int = 25565\n',
	'const DEFAULT_LAN_PORT: int = 25565\nvar lan_discovery: LanDiscovery\n'
)
backend_text = backend_text.replace(
	'\tmultiplayer.connection_failed.connect(_on_connection_failed)\n',
	'\tmultiplayer.connection_failed.connect(_on_connection_failed)\n\tlan_discovery = LanDiscovery.new()\n\tadd_child(lan_discovery)\n'
)
backend_text = backend_text.replace(
	'\tdirect_connect_address = "%s:%d" % [host_ip, host_port]\n',
	'\tdirect_connect_address = "%s:%d" % [host_ip, host_port]\n\tvar discovery_error: Error = lan_discovery.start_host(host_port, player_name)\n\tif discovery_error != OK:\n\t\tpush_warning("LAN discovery could not bind UDP port %d: %s" % [LanDiscovery.DISCOVERY_PORT, error_string(discovery_error)])\n'
)
backend_text = backend_text.replace(
	'\tmultiplayer_peer.close()\n\tmultiplayer_peer = null\n',
	'\tif lan_discovery:\n\t\tlan_discovery.stop_host()\n\tmultiplayer_peer.close()\n\tmultiplayer_peer = null\n'
)
backend_file.write_text(backend_text, encoding="utf-8")

# Put LAN HOST/JOIN inside LOCAL; retain the existing local-play modes below them.
main_menu_path = "scenes/main_menu/scripts/main_menu.gd"
main_menu_text = (root / main_menu_path).read_text(encoding="utf-8")
if "func _install_local_lan_buttons" not in main_menu_text:
	main_menu_text = main_menu_text.replace(
		'\tname_edit.text = str(Globals.settings.get("player_name", ""))\n',
		'\tname_edit.text = str(Globals.settings.get("player_name", ""))\n\tstart_join_button.visible = false\n\t_install_local_lan_buttons()\n'
	)
	main_menu_text += r'''

func _install_local_lan_buttons() -> void:
	var parent: VBoxContainer = local_original_button.get_parent() as VBoxContainer
	if parent == null:
		return
	var host_button: Button = Button.new()
	host_button.name = "LocalHostButton"
	host_button.text = "HOST"
	host_button.theme = local_original_button.theme
	host_button.flat = true
	host_button.alignment = HORIZONTAL_ALIGNMENT_LEFT
	host_button.custom_minimum_size.y = 42
	parent.add_child(host_button)
	parent.move_child(host_button, 0)
	host_button.pressed.connect(func():
		_save_player_name()
		set_current_active_container(&"Host")
	)
	var join_button: Button = Button.new()
	join_button.name = "LocalJoinButton"
	join_button.text = "JOIN"
	join_button.theme = local_original_button.theme
	join_button.flat = true
	join_button.alignment = HORIZONTAL_ALIGNMENT_LEFT
	join_button.custom_minimum_size.y = 42
	parent.add_child(join_button)
	parent.move_child(join_button, 1)
	join_button.pressed.connect(func():
		_show_lan_join_dialog()
	)

'''
(root / main_menu_path).write_text(main_menu_text, encoding="utf-8")

# Add discovery results to the manual join dialog, keeping the IP field and JOIN
# button in the upper portion so Android's virtual keyboard does not cover them.
main_menu_text = (root / main_menu_path).read_text(encoding="utf-8")
main_menu_text = main_menu_text.replace(
	'popup.size = Vector2i(560, 270)',
	'popup.size = Vector2i(620, 430)'
)
main_menu_text = main_menu_text.replace(
	'layout.add_child(address_input)\n\n\tvar buttons: HBoxContainer',
	'layout.add_child(address_input)\n\n\tvar buttons: HBoxContainer'
)
main_menu_text = main_menu_text.replace(
	'\tvar cancel_button: Button = Button.new()\n',
	'\tvar discovery_status: Label = Label.new()\n\tdiscovery_status.text = "Searching for local games..."\n\tlayout.add_child(discovery_status)\n\tvar discovered_list: VBoxContainer = VBoxContainer.new()\n\tlayout.add_child(discovered_list)\n\n\tvar cancel_button: Button = Button.new()\n'
)
main_menu_text = main_menu_text.replace(
	'\tpopup.popup_centered()\n\taddress_input.call_deferred("grab_focus")',
	'''\tvar viewport_size: Vector2i = DisplayServer.window_get_size()
	popup.position = Vector2i(max(8, (viewport_size.x - 620) / 2), 18)
	popup.popup()
	address_input.call_deferred("grab_focus")
	_scan_lan_hosts(discovery_status, discovered_list, address_input, popup)'''
)
if "func _scan_lan_hosts" not in main_menu_text:
	main_menu_text += r'''

func _scan_lan_hosts(status_label: Label, results_container: VBoxContainer, address_input: LineEdit, popup: PopupPanel) -> void:
	var scanner: LanDiscovery = LanDiscovery.new()
	add_child(scanner)
	var hosts: Array[Dictionary] = await scanner.discover_hosts(2.5)
	scanner.queue_free()
	if not is_instance_valid(popup):
		return
	for child in results_container.get_children():
		child.queue_free()
	if hosts.is_empty():
		status_label.text = "No local games found. Enter the host IP:PORT manually."
		return
	status_label.text = "LOCAL GAMES FOUND"
	for host in hosts:
		var host_button: Button = Button.new()
		host_button.text = "%s  —  %s" % [host.get("name", "Machine Party"), host.get("address", "")]
		host_button.custom_minimum_size.y = 42
		results_container.add_child(host_button)
		host_button.pressed.connect(func():
			address_input.text = str(host.get("address", ""))
			popup.queue_free()
			_join_with_address_or_code(address_input.text)
		)
'''
(root / main_menu_path).write_text(main_menu_text, encoding="utf-8")

# Show the actual host endpoint in a persistent top banner with a dedicated copy action.
lobby_path = "scenes/lobby/scripts/lobby_scene.gd"
lobby_text = (root / lobby_path).read_text(encoding="utf-8")
lobby_text = lobby_text.replace(
	'\t\tplayer_order_by_seat = {0: 1, 1: -1, 2: -1, 3: -1}\n',
	'\t\t_show_lan_address_banner()\n\t\tplayer_order_by_seat = {0: 1, 1: -1, 2: -1, 3: -1}\n'
)
if "func _show_lan_address_banner" not in lobby_text:
	lobby_text += r'''

func _show_lan_address_banner() -> void:
	if not NetworkManager.active_backend:
		return
	var address: String = NetworkManager.active_backend.direct_connect_address
	if address.is_empty():
		return
	var layer := CanvasLayer.new()
	layer.name = "LANAddressBanner"
	layer.layer = 100
	add_child(layer)
	var panel := PanelContainer.new()
	panel.set_anchors_preset(Control.PRESET_CENTER_TOP)
	panel.offset_left = -250
	panel.offset_right = 250
	panel.offset_top = 14
	panel.offset_bottom = 82
	layer.add_child(panel)
	var row := HBoxContainer.new()
	row.alignment = BoxContainer.ALIGNMENT_CENTER
	row.add_theme_constant_override("separation", 18)
	panel.add_child(row)
	var label := Label.new()
	label.text = "LOCAL GAME  " + address
	label.add_theme_font_size_override("font_size", 22)
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(label)
	var copy_button := Button.new()
	copy_button.text = "COPY"
	copy_button.custom_minimum_size = Vector2(100, 44)
	row.add_child(copy_button)
	copy_button.pressed.connect(func():
		DisplayServer.clipboard_set(address)
		copy_button.text = "COPIED"
	)
'''
(root / lobby_path).write_text(lobby_text, encoding="utf-8")

# Keep the customization screen clear of the movement/action cluster; leave only
# the dedicated SPACE and ESC controls available there.
controls_path = "controls_android.gd"
controls_text = (root / controls_path).read_text(encoding="utf-8")
if "func _refresh_context_visibility" not in controls_text:
	controls_text += r'''

func _process(_delta: float) -> void:
	_refresh_context_visibility()

func _refresh_context_visibility() -> void:
	var scene := get_tree().current_scene
	if scene == null:
		return
	var customization: bool = scene.scene_file_path.contains("character_customization")
	for name in controls:
		var control: Node = controls[name]
		if not control is CanvasItem:
			continue
		if customization:
			control.visible = name in ["TouchScreenButton5", "TouchScreenButton6"]
		else:
			control.visible = true
'''
(root / controls_path).write_text(controls_text, encoding="utf-8")

# A conservative Galaxy J7 Prime profile: 30 FPS, fewer dynamic lights and
# reduced 3D render scale; retain the already-tested GL Compatibility renderer.
optimizer_path = root / "AllInOneMobileOptimizer.gd"
optimizer_text = optimizer_path.read_text(encoding="utf-8")
optimizer_text = optimizer_text.replace("@export var max_lights: int = 8", "@export var max_lights: int = 4")
optimizer_text = optimizer_text.replace("@export var target_mobile_fps: int = 60", "@export var target_mobile_fps: int = 30")
optimizer_text = optimizer_text.replace(
	'\t\tEngine.max_fps = target_mobile_fps\n',
	'\t\tEngine.max_fps = target_mobile_fps\n\t\tget_viewport().scaling_3d_scale = 0.75\n'
)
optimizer_path.write_text(optimizer_text, encoding="utf-8")

mobile_boost_path = root / "MobileBoost.gd"
if mobile_boost_path.exists():
	mobile_boost_text = mobile_boost_path.read_text(encoding="utf-8")
	mobile_boost_text = mobile_boost_text.replace(
		'\tvar vp: = get_viewport()\n',
		'\tvar vp: = get_viewport()\n\tvp.scaling_3d_scale = 0.75\n'
	)
	mobile_boost_text = mobile_boost_text.replace(
		'RenderingServer.directional_shadow_atlas_set_size(1024, true)',
		'RenderingServer.directional_shadow_atlas_set_size(512, true)'
	)
	mobile_boost_path.write_text(mobile_boost_text, encoding="utf-8")

# Keep host name committed before the scene transition from every host mode.
host_signals = (
	'\thost_original_button.pressed.connect( func():\n',
	'\thost_custom_button.pressed.connect( func():\n',
	'\thost_arcade_button.pressed.connect( func():\n',
)
for marker in host_signals:
	if marker in main_menu_text:
		main_menu_text = main_menu_text.replace(marker, marker + '\n\t\t_save_player_name()\n', 1)
(root / main_menu_path).write_text(main_menu_text, encoding="utf-8")



# Center the touch-control editor panel along the top edge instead of occupying
# the left side of the screen, and keep focus navigation off the hidden START/JOIN.
patch(
    "scenes/main_menu/main_menu.tscn",
    '[node name="HBoxContainer" type="HBoxContainer" parent="MainMenuScene/MarginContainer/EditControls"]\nlayout_mode = 1\nanchors_preset = 15',
    '[node name="HBoxContainer" type="HBoxContainer" parent="MainMenuScene/MarginContainer/EditControls"]\nlayout_mode = 1\nanchors_preset = 15\nalignment = 1',
)
patch(
    "scenes/main_menu/main_menu.tscn",
    'theme_override_constants/margin_left = 64\ntheme_override_constants/margin_top = 64',
    'theme_override_constants/margin_left = 20\ntheme_override_constants/margin_top = 16\ntheme_override_constants/margin_right = 20',
)
patch(
    "scenes/main_menu/main_menu.tscn",
    'focus_neighbor_bottom = NodePath("../StartJoinButton")',
    'focus_neighbor_bottom = NodePath("../StartLocalButton")',
)
patch(
    "scenes/main_menu/main_menu.tscn",
    'focus_neighbor_top = NodePath("../StartJoinButton")',
    'focus_neighbor_top = NodePath("../StartHostButton")',
)



# Surface host startup failures instead of leaving the lobby on an endless loading screen.
lobby_path = "scenes/lobby/scripts/lobby_scene.gd"
lobby_text = (root / lobby_path).read_text(encoding="utf-8")
if "NetworkManager.on_matchmaking_error.connect(_on_matchmaking_error)" not in lobby_text:
	lobby_text = lobby_text.replace(
		'\tNetworkManager.on_host_disconnected.connect(_on_host_disconnected)\n',
		'\tNetworkManager.on_host_disconnected.connect(_on_host_disconnected)\n\tNetworkManager.on_matchmaking_error.connect(_on_matchmaking_error)\n'
	)
if "func _on_matchmaking_error" not in lobby_text:
	lobby_text += r'''

func _on_matchmaking_error(message: String) -> void:
	loading = false
	if load_timeout_timer:
		load_timeout_timer.stop()
	NetworkManager.leave_lobby()
	NetworkManager.cancel(1)
	GameManager.main_menu_message = message
	GlobalOverlay.fade_overlay(Color.BLACK, 0.5)
	set_ambience(false)
	await GlobalOverlay.fade_finished
	if is_inside_tree():
		get_tree().change_scene_to_file("res://scenes/main_menu/main_menu.tscn")
'''
(root / lobby_path).write_text(lobby_text, encoding="utf-8")

print("LAN patch applied successfully with local discovery, LAN menu, touch layout and J7 performance profile.")

