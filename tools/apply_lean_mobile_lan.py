#!/usr/bin/env python3
"""Apply clean Android/LAN presentation features to a disposable recovered copy.

Does not remove game content. The source recovery archive remains untouched.
"""
from __future__ import annotations
import argparse
from pathlib import Path

def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")

def append_once(path: Path, marker: str, block: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    path.write_text(text.rstrip() + "\n\n" + block.strip() + "\n", encoding="utf-8")

def write_new_or_verify(path: Path, content: str, marker: str, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if marker in text:
            return
        raise SystemExit(f"{label}: existing file has unexpected content: {path}")
    path.write_text(content.strip() + "\n", encoding="utf-8")

def apply(project: Path) -> None:
    project = project.resolve()
    if not (project / "project.godot").is_file():
        raise SystemExit(f"project.godot missing: {project}")

    discovery = r'''
extends Node
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
	_host_port = 0
	_host_name = ""

func _process(_delta: float) -> void:
	if not _host_socket:
		return
	while _host_socket.get_available_packet_count() > 0:
		var packet: String = _host_socket.get_packet().get_string_from_utf8().strip_edges()
		var sender_ip: String = _host_socket.get_packet_ip()
		var sender_port: int = _host_socket.get_packet_port()
		if packet != DISCOVER_PACKET or sender_ip.is_empty() or sender_port < 1:
			continue
		var reply := HOST_PREFIX + JSON.stringify({
			"name": _host_name,
			"port": _host_port
		})
		_host_socket.set_dest_address(sender_ip, sender_port)
		_host_socket.put_packet(reply.to_utf8_buffer())

func discover_hosts(timeout_seconds: float = 2.5) -> Array[Dictionary]:
	var socket := PacketPeerUDP.new()
	socket.set_broadcast_enabled(true)
	var bind_error: Error = socket.bind(0, "*")
	if bind_error != OK:
		socket.close()
		return []

	socket.set_dest_address("255.255.255.255", DISCOVERY_PORT)
	var send_error: Error = socket.put_packet(DISCOVER_PACKET.to_utf8_buffer())
	if send_error != OK:
		socket.close()
		return []

	var found: Array[Dictionary] = []
	var seen: Dictionary = {}
	var deadline: int = Time.get_ticks_msec() + int(timeout_seconds * 1000.0)
	while Time.get_ticks_msec() < deadline:
		while socket.get_available_packet_count() > 0:
			var response: String = socket.get_packet().get_string_from_utf8().strip_edges()
			var sender_ip: String = socket.get_packet_ip()
			if not response.begins_with(HOST_PREFIX) or not sender_ip.is_valid_ip_address():
				continue
			var parsed: Variant = JSON.parse_string(response.substr(HOST_PREFIX.length()))
			if not parsed is Dictionary:
				continue
			var port: int = int(parsed.get("port", 0))
			if port < 1 or port > 65535:
				continue
			var address: String = "%s:%d" % [sender_ip, port]
			if seen.has(address):
				continue
			seen[address] = true
			found.append({
				"name": str(parsed.get("name", "Machine Party")),
				"address": address
			})
		await get_tree().process_frame
	socket.close()
	return found

func _exit_tree() -> void:
	stop_host()
'''
    backend = r'''
extends "res://modules/multiplayer/backends/online_enet_backend.gd"
class_name LanEnetBackend

const DEFAULT_LAN_PORT: int = 25565
var lan_discovery: LanDiscovery

func _ready() -> void:
	reconnect_check_property = "name"
	multiplayer.peer_connected.connect(_on_player_connected)
	multiplayer.peer_disconnected.connect(_on_player_disconnected)
	multiplayer.server_disconnected.connect(_on_host_disconnected)
	multiplayer.connected_to_server.connect(_on_connected_to_server)
	multiplayer.connection_failed.connect(_on_connection_failed)
	lan_discovery = LanDiscovery.new()
	add_child(lan_discovery)

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
		multiplayer.multiplayer_peer = null
		hosting = false
		joinable = false
		on_matchmaking_error.emit("No local Wi-Fi or hotspot IPv4 address was found.")
		return

	direct_connect_address = "%s:%d" % [host_ip, host_port]
	var discovery_error: Error = lan_discovery.start_host(host_port, player_name)
	if discovery_error != OK:
		push_warning("LAN discovery unavailable on UDP %d: %s. Manual IP:PORT still works." % [LanDiscovery.DISCOVERY_PORT, error_string(discovery_error)])
	print("Machine Party LAN host: %s" % direct_connect_address)
	on_server_created.emit()

func join_client_direct(player_name: String, address: String) -> void:
	var cleaned: String = address.strip_edges()
	var parts: PackedStringArray = cleaned.split(":")
	if parts.size() != 2 or not parts[0].is_valid_ip_address() or not parts[1].is_valid_int():
		on_matchmaking_error.emit("Invalid LAN address. Use IP:PORT.")
		join_refused.emit(0, NetworkManager.RefuseReason.None)
		return
	var port: int = parts[1].to_int()
	if port < 1 or port > 65535:
		on_matchmaking_error.emit("Invalid LAN port.")
		join_refused.emit(0, NetworkManager.RefuseReason.None)
		return

	multiplayer_peer = ENetMultiplayerPeer.new()
	var client_error: Error = multiplayer_peer.create_client(parts[0], port)
	if client_error != OK:
		on_matchmaking_error.emit("Could not connect to local host (%d)." % client_error)
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

func join_client(player_name: String, address: String = "") -> void:
	join_client_direct(player_name, address)

func leave_lobby() -> void:
	if lan_discovery:
		lan_discovery.stop_host()
	super.leave_lobby()

func _exit_tree() -> void:
	if lan_discovery:
		lan_discovery.stop_host()
'''
    write_new_or_verify(project / "modules/multiplayer/backends/lan_discovery.gd", discovery, "class_name LanDiscovery", "LAN discovery")
    write_new_or_verify(project / "modules/multiplayer/backends/lan_enet_backend.gd", backend, "class_name LanEnetBackend", "LAN backend")

    manager = project / "modules/multiplayer/network_manager.gd"
    replace_once(
        manager,
        "enum NetworkBackendType{\n\tEnet, Steam, Online, Relay\n}",
        "enum NetworkBackendType{\n\tEnet, Steam, Online, Relay, Lan\n}",
        "Network backend enum",
    )
    replace_once(
        manager,
        "\t\tNetworkBackendType.Steam:\n",
        '\t\tNetworkBackendType.Lan:\n\n\t\t\tset_active_backend(preload("res://modules/multiplayer/backends/lan_enet_backend.gd"))\n\n\t\tNetworkBackendType.Steam:\n',
        "LAN backend selection",
    )
    replace_once(
        manager,
        '''func _is_direct_address(value: String) -> bool:
	var cleaned: String = value.strip_edges()
	if cleaned.count(":") != 1:
		return false
	var parts: PackedStringArray = cleaned.split(":")
	return parts.size() == 2 and parts[0].is_valid_ip_address() and parts[1].is_valid_int()

func backend_type_for_join_code(code: String) -> NetworkBackendType:
	var cleaned: String = code.strip_edges()
	if cleaned.is_valid_int() and cleaned.length() == 18 and Steamworks.is_initialized:
		return NetworkBackendType.Steam
	return NetworkBackendType.Relay''',
        '''func _is_direct_address(value: String) -> bool:
	var cleaned: String = value.strip_edges()
	if cleaned.count(":") != 1:
		return false
	var parts: PackedStringArray = cleaned.split(":")
	if parts.size() != 2 or not parts[0].is_valid_ip_address() or not parts[1].is_valid_int():
		return false
	var port: int = parts[1].to_int()
	return port >= 1 and port <= 65535

func backend_type_for_join_code(code: String) -> NetworkBackendType:
	var cleaned: String = code.strip_edges()
	if _is_direct_address(cleaned):
		return NetworkBackendType.Lan
	if cleaned.is_valid_int() and cleaned.length() == 18 and Steamworks.is_initialized:
		return NetworkBackendType.Steam
	return NetworkBackendType.Relay''',
        "Direct address routing",
    )

    game_manager = project / "autoloads/game_manager.gd"
    replace_once(
        game_manager,
        "var host_game: bool = false\n",
        "var host_game: bool = false\nvar lan_multiplayer_enabled: bool = false\n",
        "LAN host mode state",
    )

    controls_path = project / "controls_android.gd"
    controls = controls_path.read_text(encoding="utf-8")
    if "func set_controls_visible(value: bool)" not in controls:
        controls = controls.replace(
            "var controls: Dictionary = {}\n",
            "var controls: Dictionary = {}\nvar _user_controls_visible: bool = true\nvar _editing_controls: bool = false\nvar _toggle_button: Button\nvar _last_scene_path: String = \"\"\n",
            1,
        )
        controls = controls.replace(
            "\t_collect_controls()\n",
            "\tprocess_mode = Node.PROCESS_MODE_ALWAYS\n\t_collect_controls()\n\t_create_toggle_button()\n\t_user_controls_visible = bool(Globals.settings.get(\"touch_controls_visible\", true))\n",
            1,
        )
        controls = controls.rstrip() + r'''

func _process(_delta: float) -> void:
	_refresh_context_visibility()

func _create_toggle_button() -> void:
	var layer: CanvasLayer = $CanvasLayer
	_toggle_button = Button.new()
	_toggle_button.name = "HideShowControlsButton"
	_toggle_button.text = "HIDE"
	_toggle_button.tooltip_text = "Hide or show touch controls"
	_toggle_button.focus_mode = Control.FOCUS_NONE
	_toggle_button.custom_minimum_size = Vector2(124, 44)
	_toggle_button.anchor_left = 1.0
	_toggle_button.anchor_right = 1.0
	_toggle_button.anchor_top = 0.0
	_toggle_button.anchor_bottom = 0.0
	_toggle_button.offset_left = -140.0
	_toggle_button.offset_top = 12.0
	_toggle_button.offset_right = -12.0
	_toggle_button.offset_bottom = 56.0
	_toggle_button.z_index = 100
	layer.add_child(_toggle_button)
	_toggle_button.pressed.connect(toggle_controls_visible)
	_toggle_button.visible = false

func set_controls_visible(value: bool) -> void:
	_user_controls_visible = value
	Globals.settings["touch_controls_visible"] = value
	Globals.save_current_settings()
	_refresh_context_visibility()

func are_controls_visible() -> bool:
	return _user_controls_visible

func toggle_controls_visible() -> void:
	set_controls_visible(not _user_controls_visible)

func _refresh_context_visibility() -> void:
	var scene := get_tree().current_scene
	var scene_path: String = scene.scene_file_path if scene else ""
	var customization: bool = scene_path.contains("character_customization")
	var in_game: bool = GameManager.in_game
	if _toggle_button:
		_toggle_button.visible = in_game and not _editing_controls
		_toggle_button.text = "HIDE" if _user_controls_visible else "SHOW"
	for control_name in controls:
		var control: Node = controls[control_name]
		if not control is CanvasItem:
			continue
		var allowed_in_context: bool = _editing_controls or in_game
		if customization and control_name in ["TouchScreenButton5", "TouchScreenButton6"]:
			allowed_in_context = true
		control.visible = allowed_in_context and (_user_controls_visible or _editing_controls)

'''
        controls = controls.replace(
            "func set_editing(editing: bool) -> void :\n\n\tfor c in controls.values():",
            "func set_editing(editing: bool) -> void :\n\n\t_editing_controls = editing\n\t_refresh_context_visibility()\n\tfor c in controls.values():",
            1,
        )
        controls_path.write_text(controls, encoding="utf-8")

    main_menu = project / "scenes/main_menu/scripts/main_menu.gd"
    replace_once(
        main_menu,
        "\tGameManager.join_lobby_id = \"\"\n",
        "\tGameManager.join_lobby_id = \"\"\n\tGameManager.lan_multiplayer_enabled = false\n",
        "Reset LAN state on main menu",
    )
    replace_once(
        main_menu,
        "\tstart_host_button.pressed.connect( func():\n\t\tset_current_active_container(&\"Host\")\n\t)",
        "\tstart_host_button.pressed.connect( func():\n\t\tGameManager.lan_multiplayer_enabled = false\n\t\tset_current_active_container(&\"Host\")\n\t)",
        "Standard host remains online",
    )
    replace_once(
        main_menu,
        "\t\tGameManager.join_lobby_id = clipboard\n\t\tGameManager.host_game = false",
        "\t\tGameManager.join_lobby_id = clipboard\n\t\tGameManager.lan_multiplayer_enabled = is_direct\n\t\tGameManager.host_game = false",
        "Clipboard direct LAN support",
    )
    replace_once(
        main_menu,
        "\tset_button_signals()\n\tset_external_scene_signals()",
        "\tset_button_signals()\n\t_install_local_lan_buttons()\n\tset_external_scene_signals()",
        "Install local LAN buttons",
    )
    append_once(
        main_menu,
        "func _install_local_lan_buttons()",
        r'''
func _install_local_lan_buttons() -> void:
	var parent: VBoxContainer = local_original_button.get_parent() as VBoxContainer
	if parent == null or parent.get_node_or_null("LocalHostButton"):
		return
	var host_button := Button.new()
	host_button.name = "LocalHostButton"
	host_button.text = "HOST"
	host_button.theme = local_original_button.theme
	host_button.flat = true
	host_button.alignment = HORIZONTAL_ALIGNMENT_LEFT
	host_button.custom_minimum_size.y = 42
	host_button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	parent.add_child(host_button)
	parent.move_child(host_button, 0)
	host_button.pressed.connect(func():
		_save_player_name()
		GameManager.lan_multiplayer_enabled = true
		set_current_active_container(&"Host")
	)
	var join_button := Button.new()
	join_button.name = "LocalJoinButton"
	join_button.text = "JOIN"
	join_button.theme = local_original_button.theme
	join_button.flat = true
	join_button.alignment = HORIZONTAL_ALIGNMENT_LEFT
	join_button.custom_minimum_size.y = 42
	join_button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	parent.add_child(join_button)
	parent.move_child(join_button, 1)
	join_button.pressed.connect(func():
		_show_lan_join_dialog()
	)
''',
        "Install HOST/JOIN menu controls",
    )
    append_once(
        main_menu,
        "func _show_lan_join_dialog(",
        r'''
func _show_lan_join_dialog() -> void:
	var previous := get_node_or_null("LANJoinOverlay")
	if previous:
		previous.queue_free()
	var overlay := CanvasLayer.new()
	overlay.name = "LANJoinOverlay"
	overlay.layer = 120
	add_child(overlay)
	var root := Control.new()
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.add_child(root)
	var backdrop := ColorRect.new()
	backdrop.color = Color(0.0, 0.0, 0.0, 0.82)
	backdrop.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	backdrop.mouse_filter = Control.MOUSE_FILTER_STOP
	root.add_child(backdrop)
	var panel := PanelContainer.new()
	panel.set_anchors_preset(Control.PRESET_TOP_WIDE)
	panel.offset_left = 12
	panel.offset_right = -12
	panel.offset_top = 12
	panel.offset_bottom = 430
	root.add_child(panel)
	var margin := MarginContainer.new()
	margin.add_theme_constant_override("margin_left", 16)
	margin.add_theme_constant_override("margin_right", 16)
	margin.add_theme_constant_override("margin_top", 12)
	margin.add_theme_constant_override("margin_bottom", 12)
	panel.add_child(margin)
	var layout := VBoxContainer.new()
	layout.add_theme_constant_override("separation", 8)
	margin.add_child(layout)
	var title := Label.new()
	title.text = "JOIN LOCAL PARTY"
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	title.add_theme_font_size_override("font_size", 24)
	layout.add_child(title)
	var hint := Label.new()
	hint.text = "Searching this Wi-Fi / hotspot. Select a host or enter its IP:PORT."
	hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	layout.add_child(hint)
	var address_input := LineEdit.new()
	address_input.name = "HostAddressInput"
	address_input.placeholder_text = "192.168.43.1:25565"
	address_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	address_input.custom_minimum_size.y = 52
	address_input.alignment = HORIZONTAL_ALIGNMENT_CENTER
	address_input.clear_button_enabled = true
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
	back_button.custom_minimum_size = Vector2(120, 46)
	buttons.add_child(back_button)
	var join_button := Button.new()
	join_button.text = "JOIN"
	join_button.custom_minimum_size = Vector2(156, 46)
	buttons.add_child(join_button)
	var discovery_status := Label.new()
	discovery_status.text = "Scanning local network..."
	discovery_status.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	layout.add_child(discovery_status)
	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size.y = 112
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	layout.add_child(scroll)
	var results := VBoxContainer.new()
	results.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(results)
	back_button.pressed.connect(func():
		overlay.queue_free()
	)
	join_button.pressed.connect(func():
		_submit_lan_join(address_input.text, overlay, discovery_status)
	)
	address_input.text_submitted.connect(func(_value: String):
		_submit_lan_join(address_input.text, overlay, discovery_status)
	)
	address_input.call_deferred("grab_focus")
	_scan_lan_hosts(discovery_status, results, address_input, overlay)

func _submit_lan_join(value: String, overlay: CanvasLayer, hint: Label) -> void:
	var address: String = value.strip_edges()
	if not NetworkManager._is_direct_address(address):
		hint.text = "Invalid address. Example: 192.168.43.1:25565"
		hint.add_theme_color_override("font_color", Color.CRIMSON)
		var field := overlay.find_child("HostAddressInput", true, false) as LineEdit
		if field:
			field.grab_focus()
		return
	GameManager.lan_multiplayer_enabled = true
	GameManager.join_lobby_id = address
	GameManager.host_game = false
	GameManager.custom_game = false
	GameManager.arcade_game = false
	overlay.queue_free()
	switch_to_lobby_scene()

func _scan_lan_hosts(status_label: Label, results_container: VBoxContainer, address_input: LineEdit, overlay: CanvasLayer) -> void:
	var scanner := LanDiscovery.new()
	add_child(scanner)
	var hosts: Array[Dictionary] = await scanner.discover_hosts(2.5)
	scanner.queue_free()
	if not is_instance_valid(overlay):
		return
	for child in results_container.get_children():
		child.queue_free()
	if hosts.is_empty():
		status_label.text = "No hosts found. You can still enter IP:PORT manually."
		return
	status_label.text = "LOCAL GAMES FOUND"
	for host in hosts:
		var host_address: String = str(host.get("address", ""))
		var host_name: String = str(host.get("name", "Machine Party"))
		var host_button := Button.new()
		host_button.text = "%s  —  %s" % [host_name, host_address]
		host_button.custom_minimum_size.y = 42
		host_button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		results_container.add_child(host_button)
		host_button.pressed.connect(func():
			address_input.text = host_address
			_submit_lan_join(host_address, overlay, status_label)
		)
''',
        "LAN join and discovery overlay",
    )

    lobby = project / "scenes/lobby/scripts/lobby_scene.gd"
    replace_once(
        lobby,
        "\tNetworkManager.active_backend_type = NetworkManager.NetworkBackendType.Relay\n\tNetworkManager.start_host(_player_name())",
        "\tif GameManager.lan_multiplayer_enabled:\n\t\tNetworkManager.active_backend_type = NetworkManager.NetworkBackendType.Lan\n\telse:\n\t\tNetworkManager.active_backend_type = NetworkManager.NetworkBackendType.Relay\n\tNetworkManager.start_host(_player_name())",
        "Select LAN or online host backend",
    )
    replace_once(
        lobby,
        "func _on_server_created():\n\n\thide_scene_roots()",
        "func _on_server_created():\n\n\tif NetworkManager.active_backend_type == NetworkManager.NetworkBackendType.Lan:\n\t\t_show_lan_address_banner()\n\n\thide_scene_roots()",
        "Show LAN host banner",
    )
    append_once(
        lobby,
        "func _show_lan_address_banner()",
        r'''
func _show_lan_address_banner() -> void:
	if not NetworkManager.active_backend or NetworkManager.active_backend.direct_connect_address.is_empty():
		return
	var previous := get_node_or_null("LANAddressBanner")
	if previous:
		previous.queue_free()
	var layer := CanvasLayer.new()
	layer.name = "LANAddressBanner"
	layer.layer = 100
	add_child(layer)
	var panel := PanelContainer.new()
	panel.set_anchors_preset(Control.PRESET_TOP_WIDE)
	panel.offset_left = 10
	panel.offset_right = -10
	panel.offset_top = 10
	panel.offset_bottom = 70
	layer.add_child(panel)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	panel.add_child(row)
	var address_label := Label.new()
	address_label.text = "LOCAL GAME  " + NetworkManager.active_backend.direct_connect_address
	address_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	address_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	address_label.add_theme_font_size_override("font_size", 18)
	row.add_child(address_label)
	var copy_button := Button.new()
	copy_button.text = "COPY IP"
	copy_button.custom_minimum_size = Vector2(104, 42)
	row.add_child(copy_button)
	var address: String = NetworkManager.active_backend.direct_connect_address
	copy_button.pressed.connect(func():
		DisplayServer.clipboard_set(address)
		copy_button.text = "COPIED"
	)
''',
        "Top LAN address banner",
    )

    # Place the existing touch editor card at the horizontal center and give it
    # usable screen-edge spacing on small phones.
    scene_file = project / "scenes/main_menu/main_menu.tscn"
    replace_once(
        scene_file,
        '[node name="HBoxContainer" type="HBoxContainer" parent="MainMenuScene/MarginContainer/EditControls"]\nlayout_mode = 1\nanchors_preset = 15\nanchor_right = 1.0',
        '[node name="HBoxContainer" type="HBoxContainer" parent="MainMenuScene/MarginContainer/EditControls"]\nlayout_mode = 1\nanchors_preset = 15\nalignment = 1\nanchor_right = 1.0',
        "Center controls editor",
    )
    replace_once(
        scene_file,
        "theme_override_constants/margin_left = 64\ntheme_override_constants/margin_top = 64",
        "theme_override_constants/margin_left = 20\ntheme_override_constants/margin_top = 16\ntheme_override_constants/margin_right = 20",
        "Controls editor safe margins",
    )

    # SSAO is unavailable in Godot's Compatibility renderer. Disable it in
    # the disposable Android copy to avoid a guaranteed unsupported-effect
    # warning and unnecessary setup work; desktop Forward+ source stays intact.
    ssao_disabled = 0
    for scene_or_environment in list(project.rglob("*.tscn")) + list(project.rglob("*.tres")):
        if ".godot" in scene_or_environment.relative_to(project).parts:
            continue
        source_text = scene_or_environment.read_text(encoding="utf-8", errors="replace")
        compatible_text, replacements = __import__("re").subn(
            r"(?m)^ssao_enabled = true$",
            "ssao_enabled = false",
            source_text,
        )
        if replacements:
            scene_or_environment.write_text(compatible_text, encoding="utf-8")
            ssao_disabled += replacements
    print(f"Disabled {ssao_disabled} SSAO flags unsupported by Android Compatibility renderer.")

    # Optional Steam extensions are not part of the Android LAN build.
    # Guard every lookup so a missing singleton never blocks app startup.
    for script_path in project.rglob("*.gd"):
        if ".godot" in script_path.relative_to(project).parts:
            continue
        script_text = script_path.read_text(encoding="utf-8", errors="replace")
        safe_text = script_text.replace(
            'Engine.get_singleton("Steam")',
            'Engine.get_singleton("Steam") if Engine.has_singleton("Steam") else null',
        )
        if safe_text != script_text:
            script_path.write_text(safe_text, encoding="utf-8")

    # Feature assertions prevent a silently incomplete output copy.
    checks = {
        "controls_android.gd": [
            "func set_controls_visible(value: bool) -> void:",
            "func toggle_controls_visible() -> void:",
            "HideShowControlsButton",
        ],
        "modules/multiplayer/backends/lan_discovery.gd": [
            "class_name LanDiscovery", "func discover_hosts(", "MACHINE_PARTY_LAN_DISCOVER_V1",
        ],
        "modules/multiplayer/backends/lan_enet_backend.gd": [
            "class_name LanEnetBackend", "lan_discovery.start_host(", "super.leave_lobby()",
        ],
        "modules/multiplayer/network_manager.gd": [
            "NetworkBackendType.Lan", "func backend_type_for_join_code(",
        ],
        "scenes/main_menu/scripts/main_menu.gd": [
            "func _install_local_lan_buttons()", "LocalHostButton", "LocalJoinButton",
            "func _show_lan_join_dialog()", "func _scan_lan_hosts(",
        ],
        "scenes/lobby/scripts/lobby_scene.gd": [
            "func _show_lan_address_banner()", "NetworkBackendType.Lan",
        ],
    }
    for rel, needles in checks.items():
        text = (project / rel).read_text(encoding="utf-8")
        missing = [needle for needle in needles if needle not in text]
        if missing:
            raise SystemExit(f"{rel}: missing feature(s): {missing}")

    print("LEAN_MOBILE_LAN_FEATURES_PASS: touch hide/show, centered editor, HOST/JOIN, UDP discovery and top IP banner are installed.")

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    args = ap.parse_args()
    apply(args.project)

if __name__ == "__main__":
    main()
