from pathlib import Path
import shutil
import subprocess
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
	popup.theme = theme

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

func _scan_lan_hosts(status_label: Label, results_container: VBoxContainer, address_input: LineEdit, popup: Node) -> void:
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



# ---------------------------------------------------------------------------
# Mobile-first performance, clear rendering, in-game quality controls and JOIN UI.
# Keep the image at native 3D scale; gain speed by removing expensive effects.
# ---------------------------------------------------------------------------

def replace_function_block(source: str, start_marker: str, end_marker: str, replacement: str, label: str) -> str:
    start = source.find(start_marker)
    if start < 0:
        raise SystemExit(f"{label}: start marker not found")
    end = source.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"{label}: end marker not found")
    return source[:start] + replacement + source[end:]


# Replace the PopupPanel JOIN form with a top-anchored CanvasLayer form.
main_menu_path = "scenes/main_menu/scripts/main_menu.gd"
main_menu_text = (root / main_menu_path).read_text(encoding="utf-8")
join_ui = r'''func _join_with_address_or_code(value: String) -> void:
	GameManager.join_lobby_id = value.strip_edges()
	GameManager.host_game = false
	GameManager.custom_game = false
	switch_to_lobby_scene()

func _show_lan_join_dialog(prefill: String = "") -> void:
	var previous: Node = get_node_or_null("LANJoinOverlay")
	if previous:
		previous.queue_free()

	var overlay := CanvasLayer.new()
	overlay.name = "LANJoinOverlay"
	overlay.layer = 110
	add_child(overlay)

	var backdrop := ColorRect.new()
	backdrop.color = Color(0.0, 0.0, 0.0, 0.82)
	backdrop.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	backdrop.mouse_filter = Control.MOUSE_FILTER_STOP
	overlay.add_child(backdrop)

	var panel := PanelContainer.new()
	panel.set_anchors_preset(Control.PRESET_TOP_WIDE)
	panel.offset_left = 16
	panel.offset_right = -16
	panel.offset_top = 16
	panel.offset_bottom = 410
	overlay.add_child(panel)

	var margin := MarginContainer.new()
	margin.add_theme_constant_override("margin_left", 18)
	margin.add_theme_constant_override("margin_right", 18)
	margin.add_theme_constant_override("margin_top", 16)
	margin.add_theme_constant_override("margin_bottom", 16)
	panel.add_child(margin)

	var layout := VBoxContainer.new()
	layout.add_theme_constant_override("separation", 10)
	margin.add_child(layout)

	var title := Label.new()
	title.text = "JOIN LOCAL PARTY"
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	title.add_theme_font_size_override("font_size", 24)
	layout.add_child(title)

	var hint := Label.new()
	hint.text = "Enter the host IP and port shown on the HOST phone."
	hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	layout.add_child(hint)

	var address_input := LineEdit.new()
	address_input.name = "HostAddressInput"
	address_input.placeholder_text = "192.168.43.1:25565"
	address_input.text = prefill.strip_edges()
	address_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	address_input.custom_minimum_size = Vector2(0, 56)
	address_input.alignment = HORIZONTAL_ALIGNMENT_CENTER
	address_input.clear_button_enabled = true
	address_input.focus_mode = Control.FOCUS_ALL
	address_input.virtual_keyboard_enabled = true
	address_input.virtual_keyboard_type = LineEdit.KEYBOARD_TYPE_URL
	address_input.select_all_on_focus = true
	layout.add_child(address_input)

	var buttons := HBoxContainer.new()
	buttons.alignment = BoxContainer.ALIGNMENT_CENTER
	buttons.add_theme_constant_override("separation", 12)
	layout.add_child(buttons)

	var back_button := Button.new()
	back_button.text = "BACK"
	back_button.custom_minimum_size = Vector2(132, 48)
	buttons.add_child(back_button)

	var join_button := Button.new()
	join_button.text = "JOIN"
	join_button.custom_minimum_size = Vector2(160, 48)
	buttons.add_child(join_button)

	var discovery_status := Label.new()
	discovery_status.text = "Searching for games on this Wi-Fi / hotspot..."
	discovery_status.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	discovery_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	layout.add_child(discovery_status)

	var discovered_list := VBoxContainer.new()
	layout.add_child(discovered_list)

	back_button.pressed.connect(func():
		overlay.queue_free()
	)
	join_button.pressed.connect(func():
		_submit_lan_join(address_input.text, overlay, discovery_status)
	)
	address_input.text_submitted.connect(func(_value: String):
		_submit_lan_join(address_input.text, overlay, discovery_status)
	)

	# The LineEdit enters the scene tree before grabbing focus, so Android can
	# display its native URL keyboard. The field and JOIN row stay at the top.
	address_input.call_deferred("grab_focus")
	_scan_lan_hosts(discovery_status, discovered_list, address_input, overlay)

func _submit_lan_join(value: String, overlay: CanvasLayer, hint: Label) -> void:
	var address: String = value.strip_edges()
	if not NetworkManager._is_direct_address(address):
		hint.text = "Invalid IP:PORT. Example: 192.168.43.1:25565"
		hint.add_theme_color_override("font_color", Color.CRIMSON)
		var address_input := overlay.find_child("HostAddressInput", true, false) as LineEdit
		if address_input:
			address_input.grab_focus()
		return

	overlay.queue_free()
	_join_with_address_or_code(address)

func _scan_lan_hosts(status_label: Label, results_container: VBoxContainer, address_input: LineEdit, overlay: Node) -> void:
	var scanner := LanDiscovery.new()
	add_child(scanner)
	var hosts: Array[Dictionary] = await scanner.discover_hosts(2.5)
	scanner.queue_free()
	if not is_instance_valid(overlay):
		return

	for child in results_container.get_children():
		child.queue_free()

	if hosts.is_empty():
		status_label.text = "No local games found. You can still enter the IP:PORT above."
		return

	status_label.text = "LOCAL GAMES FOUND"
	for host in hosts:
		var host_address: String = str(host.get("address", ""))
		var host_button := Button.new()
		host_button.text = "%s  —  %s" % [host.get("name", "Machine Party"), host_address]
		host_button.custom_minimum_size.y = 42
		results_container.add_child(host_button)
		host_button.pressed.connect(func():
			address_input.text = host_address
			_submit_lan_join(address_input.text, overlay, status_label)
		)

'''
main_menu_text = replace_function_block(
    main_menu_text,
    "func _join_with_address_or_code(value: String) -> void:",
    "func switch_to_lobby_scene():",
    join_ui,
    "LAN join form",
)

# Older LAN patch already appended a discovery helper. Keep only the latest
# CanvasLayer-compatible implementation to prevent duplicate GDScript methods.
scan_marker = "func _scan_lan_hosts("
scan_positions = []
scan_cursor = 0
while True:
    scan_position = main_menu_text.find(scan_marker, scan_cursor)
    if scan_position < 0:
        break
    scan_positions.append(scan_position)
    scan_cursor = scan_position + len(scan_marker)
if len(scan_positions) > 1:
    first_start = scan_positions[0]
    first_end = main_menu_text.find("\nfunc ", first_start + len(scan_marker))
    if first_end < 0:
        main_menu_text = main_menu_text[:first_start].rstrip() + "\n"
    else:
        main_menu_text = main_menu_text[:first_start] + main_menu_text[first_end + 1:]
if main_menu_text.count(scan_marker) != 1:
    raise SystemExit("main_menu.gd: LAN discovery helper is not unique")
(root / main_menu_path).write_text(main_menu_text, encoding="utf-8")


# Restore crisp 3D scale on mobile even when an old saved config contains 0.6.
globals_path = "autoloads/globals.gd"
patch(globals_path, '"scaling_3d_scale": 0.6', '"scaling_3d_scale": 1.0')
globals_text = (root / globals_path).read_text(encoding="utf-8")
globals_text = replace_function_block(
    globals_text,
    "func set_scaling_3d_scale(_value: float):",
    "func enable_debug_tools():",
    '''func set_scaling_3d_scale(_value: float):
	if OS.has_feature("mobile") or OS.get_name() in ["Android", "iOS"]:
		_value = 1.0
	settings.set("scaling_3d_scale", _value)
	get_window().scaling_3d_scale = clampf(_value, 0.1, 1.0)

''',
    "mobile render scale",
)
(root / globals_path).write_text(globals_text, encoding="utf-8")


# Reduce rendering cost without downscaling the image.
project_file = root / "project.godot"
project_lines = project_file.read_text(encoding="utf-8").splitlines()
rendering_index = next((i for i, line in enumerate(project_lines) if line.strip() == "[rendering]"), -1)
if rendering_index < 0:
    raise SystemExit("project.godot: [rendering] section missing")

render_settings = {
    "scaling_3d/scale": "scaling_3d/scale=1.0",
    "lights_and_shadows/directional_shadow/size": "lights_and_shadows/directional_shadow/size=512",
    "lights_and_shadows/directional_shadow/soft_shadow_filter_quality": "lights_and_shadows/directional_shadow/soft_shadow_filter_quality=0",
    "lights_and_shadows/positional_shadow/atlas_size": "lights_and_shadows/positional_shadow/atlas_size=512",
}
for key, setting in render_settings.items():
    found = next(
        (i for i, line in enumerate(project_lines[rendering_index + 1:], rendering_index + 1)
         if line.strip().startswith(key + "=")),
        -1,
    )
    if found >= 0:
        project_lines[found] = setting
    else:
        project_lines.insert(rendering_index + 1, setting)

autoload_index = next((i for i, line in enumerate(project_lines) if line.strip() == "[autoload]"), -1)
if autoload_index < 0:
    raise SystemExit("project.godot: [autoload] section missing")
if not any(line.startswith("MobileGameSettings=") for line in project_lines):
    optimizer_line = next(
        (i for i, line in enumerate(project_lines[autoload_index + 1:], autoload_index + 1)
         if line.startswith("AllInOneMobileOptimizer=")),
        -1,
    )
    if optimizer_line >= 0:
        project_lines.insert(optimizer_line + 1, 'MobileGameSettings="*res://MobileGameSettings.gd"')
    else:
        project_lines.insert(autoload_index + 1, 'MobileGameSettings="*res://MobileGameSettings.gd"')
project_file.write_text("\n".join(project_lines) + "\n", encoding="utf-8")


# Respect the user's touch-control visibility preference without adding a
# second _process callback to projects that already define one.
controls_path = "controls_android.gd"
controls_text = (root / controls_path).read_text(encoding="utf-8")
import re

# Remove every earlier generated copy of these helpers, even if an original
# _process function appears after them in the recovered script.
for function_name in ("set_controls_visible", "are_controls_visible", "toggle_controls_visible", "_refresh_context_visibility"):
    pattern = rf"(?ms)^func {function_name}\([^\n]*?\)(?:\s*->\s*[^:\n]+)?\s*:\n.*?(?=^(?:func |static func |signal |var |const |@|class_name |extends )|\Z)"
    controls_text = re.sub(pattern, "", controls_text)

if "var _user_controls_visible: bool" not in controls_text:
    controls_text, count = re.subn(
        r"(?m)^(var controls[^\n]*\n)",
        r"\1var _user_controls_visible: bool = true\nvar _default_control_visibility: Dictionary = {}\n",
        controls_text,
        count=1,
    )
    if count != 1:
        raise SystemExit("controls_android.gd: could not add visibility preference state")

controls_functions = r'''func set_controls_visible(value: bool) -> void:
	_user_controls_visible = value
	_refresh_context_visibility()

func are_controls_visible() -> bool:
	return _user_controls_visible

func toggle_controls_visible() -> void:
	set_controls_visible(not _user_controls_visible)

func _refresh_context_visibility() -> void:
	var scene := get_tree().current_scene
	if scene == null:
		return
	var customization: bool = scene.scene_file_path.contains("character_customization")
	for control_name in controls:
		var control: Node = controls[control_name]
		if not control is CanvasItem:
			continue
		var control_id: int = control.get_instance_id()
		if not _default_control_visibility.has(control_id):
			_default_control_visibility[control_id] = control.visible
		var allowed_in_customization: bool = control_name in ["TouchScreenButton5", "TouchScreenButton6"]
		var default_visible: bool = bool(_default_control_visibility[control_id])
		control.visible = _user_controls_visible and default_visible and (not customization or allowed_in_customization)
'''
controls_text = controls_text.rstrip() + "\n\n" + controls_functions + "\n"
(root / controls_path).write_text(controls_text, encoding="utf-8")


# In-game graphics menu: Auto starts with the low-effects profile on every phone.
# The native render scale stays at 1.0, so the minimum profile remains sharp.
mobile_settings = r'''extends Node

enum QualityProfile { AUTO, LOW, MEDIUM, HIGH }

const CONFIG_PATH: String = "user://mobile_game_settings.cfg"
const PROFILE_LABELS: Array[String] = ["AUTO", "LOW", "MEDIUM", "HIGH"]

var quality_profile: int = QualityProfile.AUTO
var _scene_root: Node
var _overlay: CanvasLayer
var _quality_button: Button
var _controls_button: Button
var _controls_shown: bool = true
var _active_light_count: int = 0
var _light_visibility_cache: Dictionary = {}
var _light_shadow_cache: Dictionary = {}
var _mesh_shadow_cache: Dictionary = {}
var _environment_cache: Dictionary = {}

func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_load_settings()
	call_deferred("_apply_controls_visibility")
	if get_tree():
		get_tree().scene_changed.connect(_on_scene_changed)
		get_tree().node_added.connect(_on_node_added)
	_apply_profile()
	call_deferred("_on_scene_changed")

func _is_mobile() -> bool:
	return OS.has_feature("mobile") or OS.get_name() in ["Android", "iOS"]

func _load_settings() -> void:
	var config := ConfigFile.new()
	if config.load(CONFIG_PATH) == OK:
		quality_profile = clampi(
			int(config.get_value("graphics", "quality_profile", QualityProfile.AUTO)),
			QualityProfile.AUTO,
			QualityProfile.HIGH
		)
		_controls_shown = bool(config.get_value("controls", "visible", true))

func _apply_controls_visibility() -> void:
	var touch_controls := get_node_or_null("/root/ControlsAndroid")
	if touch_controls and touch_controls.has_method("set_controls_visible"):
		touch_controls.set_controls_visible(_controls_shown)

func _save_settings() -> void:
	var config := ConfigFile.new()
	config.set_value("graphics", "quality_profile", quality_profile)
	config.set_value("controls", "visible", _controls_shown)
	var error: Error = config.save(CONFIG_PATH)
	if error != OK:
		push_warning("Could not save mobile game settings: %s" % error_string(error))

func _on_scene_changed() -> void:
	_scene_root = get_tree().current_scene
	_apply_controls_visibility()
	_light_visibility_cache.clear()
	_light_shadow_cache.clear()
	_mesh_shadow_cache.clear()
	_environment_cache.clear()
	_active_light_count = 0
	_apply_profile()
	_remove_overlay()
	if _is_mobile() and _is_minigame_scene(_scene_root):
		_create_overlay()

func _is_minigame_scene(scene: Node) -> bool:
	if scene == null:
		return false
	if scene.scene_file_path.begins_with("res://minigames/"):
		return true
	return GameManager.in_game

func _on_node_added(node: Node) -> void:
	if not _is_mobile() or _scene_root == null or node == _scene_root:
		return
	if not _scene_root.is_ancestor_of(node):
		return
	_apply_quality_to_node(node)

func _apply_profile() -> void:
	if not _is_mobile():
		return

	var fps_limit: int = 30
	match quality_profile:
		QualityProfile.AUTO, QualityProfile.LOW:
			fps_limit = 30
		QualityProfile.MEDIUM:
			fps_limit = 45
		QualityProfile.HIGH:
			fps_limit = 60

	Engine.max_fps = fps_limit
	var viewport := get_viewport()
	viewport.msaa_3d = Viewport.MSAA_DISABLED
	viewport.screen_space_aa = Viewport.SCREEN_SPACE_AA_DISABLED
	get_window().scaling_3d_scale = 1.0

	RenderingServer.positional_soft_shadow_filter_set_quality(RenderingServer.SHADOW_QUALITY_HARD)
	RenderingServer.directional_soft_shadow_filter_set_quality(RenderingServer.SHADOW_QUALITY_HARD)
	RenderingServer.directional_shadow_atlas_set_size(512, true)

	if _scene_root:
		_active_light_count = 0
		_apply_quality_to_tree(_scene_root)

func _max_dynamic_lights() -> int:
	match quality_profile:
		QualityProfile.AUTO, QualityProfile.LOW:
			return 2
		QualityProfile.MEDIUM:
			return 4
		QualityProfile.HIGH:
			return 8
	return 2

func _shadows_enabled() -> bool:
	return quality_profile == QualityProfile.HIGH

func _apply_quality_to_tree(node: Node) -> void:
	_apply_quality_to_node(node)
	for child in node.get_children():
		_apply_quality_to_tree(child)

func _apply_quality_to_node(node: Node) -> void:
	if node is GeometryInstance3D:
		var mesh_id: int = node.get_instance_id()
		if not _mesh_shadow_cache.has(mesh_id):
			_mesh_shadow_cache[mesh_id] = node.cast_shadow
		node.cast_shadow = (
			_mesh_shadow_cache[mesh_id]
			if _shadows_enabled()
			else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		)

	if node is Light3D:
		var light: Light3D = node
		var light_id: int = light.get_instance_id()
		if not _light_visibility_cache.has(light_id):
			_light_visibility_cache[light_id] = light.visible
			_light_shadow_cache[light_id] = light.shadow_enabled

		light.shadow_enabled = _light_shadow_cache[light_id] and _shadows_enabled()
		if light is DirectionalLight3D:
			light.visible = _light_visibility_cache[light_id]
		else:
			var allowed: bool = _active_light_count < _max_dynamic_lights()
			light.visible = _light_visibility_cache[light_id] and allowed
			if _light_visibility_cache[light_id]:
				_active_light_count += 1

	if node is WorldEnvironment and node.environment:
		var environment: Environment = node.environment
		var environment_id: int = environment.get_instance_id()
		if not _environment_cache.has(environment_id):
			_environment_cache[environment_id] = {
				"ssao_enabled": environment.ssao_enabled,
				"ssr_enabled": environment.ssr_enabled,
				"glow_enabled": environment.glow_enabled,
				"sdfgi_enabled": environment.sdfgi_enabled,
				"volumetric_fog_enabled": environment.volumetric_fog_enabled
			}
		var state: Dictionary = _environment_cache[environment_id]
		# Godot Compatibility renderer on Android does not support SSAO.
		# Force it off for every mobile quality preset, including HIGH, so
		# unsupported screen-space effects cannot break or stall scene rendering.
		environment.ssao_enabled = false
		environment.ssr_enabled = false
		environment.sdfgi_enabled = false
		environment.volumetric_fog_enabled = false
		environment.glow_enabled = state["glow_enabled"] and _shadows_enabled()

func _create_overlay() -> void:
	if _overlay:
		return
	_overlay = CanvasLayer.new()
	_overlay.name = "InGameMobileSettings"
	_overlay.layer = 90
	add_child(_overlay)

	var panel := PanelContainer.new()
	panel.set_anchors_preset(Control.PRESET_TOP_RIGHT)
	panel.offset_left = -334
	panel.offset_right = -10
	panel.offset_top = 10
	panel.offset_bottom = 62
	_overlay.add_child(panel)

	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	panel.add_child(row)

	_quality_button = Button.new()
	_quality_button.custom_minimum_size = Vector2(154, 42)
	_quality_button.pressed.connect(_cycle_quality)
	row.add_child(_quality_button)

	_controls_button = Button.new()
	_controls_button.custom_minimum_size = Vector2(144, 42)
	_controls_button.pressed.connect(_toggle_controls)
	row.add_child(_controls_button)
	_update_overlay_labels()

func _remove_overlay() -> void:
	if _overlay:
		_overlay.queue_free()
	_overlay = null
	_quality_button = null
	_controls_button = null

func _cycle_quality() -> void:
	quality_profile = (quality_profile + 1) % (QualityProfile.HIGH + 1)
	_apply_profile()
	_save_settings()
	_update_overlay_labels()

func _toggle_controls() -> void:
	_controls_shown = not _controls_shown
	var touch_controls := get_node_or_null("/root/ControlsAndroid")
	if touch_controls and touch_controls.has_method("set_controls_visible"):
		touch_controls.set_controls_visible(_controls_shown)
	_save_settings()
	_update_overlay_labels()

func _update_overlay_labels() -> void:
	if is_instance_valid(_quality_button):
		_quality_button.text = "QUALITY: " + PROFILE_LABELS[quality_profile]
	if is_instance_valid(_controls_button):
		_controls_button.text = "HIDE CONTROLS" if _controls_shown else "SHOW CONTROLS"

func _exit_tree() -> void:
	if get_tree() and get_tree().node_added.is_connected(_on_node_added):
		get_tree().node_added.disconnect(_on_node_added)
'''
(root / "MobileGameSettings.gd").write_text(mobile_settings, encoding="utf-8")


# Keep the legacy optimizer consistent with the new manager, not blurry.
optimizer_path = root / "AllInOneMobileOptimizer.gd"
optimizer_text = optimizer_path.read_text(encoding="utf-8")
optimizer_text = optimizer_text.replace("@export var max_lights: int = 8", "@export var max_lights: int = 2")
optimizer_text = optimizer_text.replace("@export var max_lights: int = 4", "@export var max_lights: int = 2")
optimizer_text = optimizer_text.replace("@export var target_mobile_fps: int = 60", "@export var target_mobile_fps: int = 30")
optimizer_text = optimizer_text.replace("\t\tget_viewport().scaling_3d_scale = 0.75\n", "")
optimizer_text = optimizer_text.replace(
	"\t\t_strip_heavy_environment_effects()\n",
	"\t\t_strip_heavy_environment_effects()\n\t\tget_viewport().msaa_3d = Viewport.MSAA_DISABLED\n\t\tget_viewport().screen_space_aa = Viewport.SCREEN_SPACE_AA_DISABLED\n\t\tget_window().scaling_3d_scale = 1.0\n",
)
optimizer_text = optimizer_text.replace(
	"\t\tenv.sdfgi_enabled = false\n",
	"\t\tenv.sdfgi_enabled = false\n\t\tenv.volumetric_fog_enabled = false\n",
)
optimizer_path.write_text(optimizer_text, encoding="utf-8")

mobile_boost_path = root / "MobileBoost.gd"
if mobile_boost_path.exists():
	mobile_boost_text = mobile_boost_path.read_text(encoding="utf-8")
	mobile_boost_text = mobile_boost_text.replace(
		"vp.screen_space_aa = Viewport.SCREEN_SPACE_AA_FXAA",
		"vp.screen_space_aa = Viewport.SCREEN_SPACE_AA_DISABLED",
	)
	mobile_boost_text = mobile_boost_text.replace("\tvp.scaling_3d_scale = 0.75\n", "")
	mobile_boost_path.write_text(mobile_boost_text, encoding="utf-8")


# Do not continue a network join after redirecting an empty join address to menu.
lobby_path = "scenes/lobby/scripts/lobby_scene.gd"
lobby_text = (root / lobby_path).read_text(encoding="utf-8")
lobby_text = lobby_text.replace(
	'\t\tget_tree().change_scene_to_file("res://scenes/main_menu/main_menu.tscn")\n\n\thide_scene_roots()',
	'\t\tget_tree().change_scene_to_file("res://scenes/main_menu/main_menu.tscn")\n\t\treturn\n\n\thide_scene_roots()',
	1,
)
(root / lobby_path).write_text(lobby_text, encoding="utf-8")


# Validate minigame scene references before attempting a scene replacement.
network_path = "modules/multiplayer/network_manager.gd"
network_text = (root / network_path).read_text(encoding="utf-8")
network_text = replace_function_block(
	network_text,
	'@rpc("any_peer", "call_remote", "reliable")\nfunc load_game_scene_rpc(is_reconnect: bool = false):',
	'@rpc("any_peer", "call_remote", "reliable")\nfunc add_player_to_join_queue_rpc',
	'''@rpc("any_peer", "call_remote", "reliable")
func load_game_scene_rpc(is_reconnect: bool = false):
	GameManager.reconnect = is_reconnect
	_change_to_game_scene()

func _change_to_game_scene() -> void:
	if game_scene == null:
		push_error("Machine Party: game_scene is empty; refusing an invalid minigame transition.")
		GameManager.main_menu_dialog_message = "The minigame could not be loaded. Return to the lobby and try again."
		get_tree().call_deferred("change_scene_to_file", "res://scenes/main_menu/main_menu.tscn")
		return

	var change_error: Error = get_tree().change_scene_to_packed(game_scene)
	if change_error != OK:
		push_error("Machine Party: change_scene_to_packed failed: %s" % error_string(change_error))
		GameManager.main_menu_dialog_message = "The minigame could not be loaded (%s)." % error_string(change_error)
		get_tree().call_deferred("change_scene_to_file", "res://scenes/main_menu/main_menu.tscn")

''',
	"minigame transition guard",
)
network_text = network_text.replace(
	'\tif not multiplayer.is_server():\n\t\tget_tree().change_scene_to_packed(game_scene)',
	'\tif not multiplayer.is_server():\n\t\t_change_to_game_scene()',
	1,
)
(root / network_path).write_text(network_text, encoding="utf-8")


# Compatibility renderer does not implement SSAO/SSR/SDFGI/volumetric fog.
# Disable these in serialized environments too, before Godot imports or instantiates them.
unsupported_environment_flags = (
    "ssao_enabled",
    "ssr_enabled",
    "sdfgi_enabled",
    "volumetric_fog_enabled",
)
for resource_file in root.rglob("*"):
    if resource_file.suffix not in {".tscn", ".tres"} or not resource_file.is_file():
        continue
    try:
        resource_text = resource_file.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    updated_text = resource_text
    for flag in unsupported_environment_flags:
        updated_text = updated_text.replace(f"{flag} = true", f"{flag} = false")
    if updated_text != resource_text:
        resource_file.write_text(updated_text, encoding="utf-8")

# Static assertions ensure future source recoveries do not silently lose features.
checks = {
	"project.godot": [
		'renderer/rendering_method.mobile="gl_compatibility"',
		'scaling_3d/scale=1.0',
		'MobileGameSettings="*res://MobileGameSettings.gd"',
	],
	"scenes/main_menu/scripts/main_menu.gd": [
		"func _show_lan_join_dialog",
		"virtual_keyboard_enabled = true",
		"LineEdit.KEYBOARD_TYPE_URL",
	],
	"controls_android.gd": [
		"func set_controls_visible(value: bool)",
		"_user_controls_visible and",
	],
	"MobileGameSettings.gd": [
		"func _cycle_quality()",
		"func _toggle_controls()",
		"Viewport.SCREEN_SPACE_AA_DISABLED",
		"environment.ssao_enabled = false",
	],
	"modules/multiplayer/network_manager.gd": [
		"func _change_to_game_scene()",
		"game_scene == null",
	],
}
for check_path, required_strings in checks.items():
	check_text = (root / check_path).read_text(encoding="utf-8")
	missing = [item for item in required_strings if item not in check_text]
	if missing:
		raise SystemExit(f"{check_path}: missing required mobile performance feature(s): {missing}")

for resource_file in root.rglob("*"):
	if resource_file.suffix not in {".tscn", ".tres"} or not resource_file.is_file():
		continue
	try:
		resource_text = resource_file.read_text(encoding="utf-8")
	except (UnicodeDecodeError, OSError):
		continue
	for flag in unsupported_environment_flags:
		if f"{flag} = true" in resource_text:
			raise SystemExit(f"{resource_file.relative_to(root)} still enables unsupported mobile effect {flag}")

audio_repair_script = Path(__file__).with_name("repair_ogg_comments.py")
if not audio_repair_script.is_file():
	raise SystemExit(f"Required audio metadata repair tool is missing: {audio_repair_script}")
if shutil.which("ffmpeg") is None:
	subprocess.run(["sudo", "apt-get", "update"], check=True)
	subprocess.run(["sudo", "apt-get", "install", "-y", "ffmpeg"], check=True)
subprocess.run([sys.executable, str(audio_repair_script), str(root)], check=True)

print("Mobile performance baseline, in-game graphics menu, touch visibility toggle, Android JOIN keyboard, minigame load guards and lossless Ogg metadata repair applied.")
