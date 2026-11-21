import shutil
import tempfile
import unittest
from pathlib import Path

from tools.prepare_local_only_project import (
    force_lan_only,
    is_music_asset,
    silence_music,
    remove_achievement_system,
    _remove_remote_backend_cases,
    _remove_unreferenced_remote_code,
)

class LocalOnlyOptimizationTests(unittest.TestCase):
    def test_music_detection_preserves_effects_and_ambience(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(is_music_asset(root / "audio/unique/music/menu.ogg", root))
            self.assertTrue(is_music_asset(root / "audio/unique/music new/loop.ogg", root))
            self.assertFalse(is_music_asset(root / "audio/sfx/click.ogg", root))
            self.assertFalse(is_music_asset(root / "audio/ambience/wind.ogg", root))

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required")
    def test_music_is_silenced_in_place_and_sfx_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            music = root / "audio/unique/music/menu.ogg"
            sfx = root / "audio/sfx/click.ogg"
            music.parent.mkdir(parents=True)
            sfx.parent.mkdir(parents=True)
            # A small valid tone is enough to verify path-preserving replacement.
            import subprocess
            subprocess.run([
                shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-c:a", "libvorbis", str(music),
            ], check=True)
            sfx.write_bytes(b"keep this exact sound effect")
            old_size = music.stat().st_size
            report = silence_music(root, shutil.which("ffmpeg"))
            self.assertEqual(report["music_tracks_silenced"], 1)
            self.assertTrue(music.exists())
            self.assertLess(music.stat().st_size, old_size)
            self.assertEqual(sfx.read_bytes(), b"keep this exact sound effect")


    def test_remote_backend_switch_cases_are_removed_and_lan_remains(self):
        source = (
            "match active_backend_type:\n"
            "\tNetworkBackendType.Enet:\n"
            "\t\tset_active_backend(preload(\"res://modules/multiplayer/backends/enet_backend.gd\"))\n"
            "\tNetworkBackendType.Steam:\n"
            "\t\tset_active_backend(preload(\"res://modules/multiplayer/backends/steam_backend.gd\"))\n"
            "\tNetworkBackendType.Relay:\n"
            "\t\tset_active_backend(preload(\"res://modules/multiplayer/backends/relay_backend.gd\"))\n"
            "\tNetworkBackendType.Lan:\n"
            "\t\tset_active_backend(preload(\"res://modules/multiplayer/backends/lan_enet_backend.gd\"))\n"
        )
        cleaned = _remove_remote_backend_cases(source)
        self.assertNotIn("NetworkBackendType.Enet:", cleaned)
        self.assertIn("NetworkBackendType.Lan:", cleaned)
        self.assertNotIn("NetworkBackendType.Steam:", cleaned)
        self.assertNotIn("NetworkBackendType.Relay:", cleaned)


    def test_nonexistent_achievement_subsystem_is_removed_not_localized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "project.godot"
            config.write_text(
                '[autoload]\nAchievementManager="*res://autoloads/achievement_manager.gd"\n'
                '[application]\nconfig/name="Machine Party"\n',
                encoding="utf-8",
            )
            manager = root / "autoloads/achievement_manager.gd"
            manager.parent.mkdir(parents=True)
            manager.write_text("extends Node\n", encoding="utf-8")
            steam = root / "modules/achievements/backends/achievement_backend_steam.gd"
            local = root / "modules/achievements/backends/achievement_backend_local.gd"
            steam.parent.mkdir(parents=True)
            steam.write_text("extends RefCounted\n", encoding="utf-8")
            local.write_text("extends RefCounted\n", encoding="utf-8")
            (steam.parent / "achievement_backend.gd").write_text(
                "extends RefCounted\n", encoding="utf-8"
            )

            report = remove_achievement_system(root)

            self.assertTrue(report["achievement_system_removed"])
            self.assertTrue(report["achievement_autoload_removed"])
            self.assertEqual(len(report["achievement_files_removed"]), 4)
            self.assertEqual(report["remaining_references"], [])
            self.assertNotIn("AchievementManager", config.read_text(encoding="utf-8"))
            self.assertFalse(manager.exists())
            self.assertFalse(steam.exists())
            self.assertFalse(local.exists())

    def test_unreferenced_remote_cleanup_keeps_lan_enet_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backends = root / "modules/multiplayer/backends"
            backends.mkdir(parents=True)
            enet = backends / "enet_backend.gd"
            enet.write_text("extends RefCounted\n", encoding="utf-8")
            steam = backends / "steam_backend.gd"
            steam.write_text("extends RefCounted\n", encoding="utf-8")
            relay = backends / "relay_backend.gd"
            relay.write_text("extends RefCounted\n", encoding="utf-8")

            removed = _remove_unreferenced_remote_code(root)

            self.assertTrue(enet.exists())
            self.assertFalse(steam.exists())
            self.assertFalse(relay.exists())
            self.assertNotIn("modules/multiplayer/backends/enet_backend.gd", removed)

    def test_join_code_routing_and_lobby_are_lan_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = root / "modules/multiplayer/network_manager.gd"
            manager.parent.mkdir(parents=True)
            manager.write_text(
                "enum NetworkBackendType{\n\tEnet, Steam, Online, Relay, Lan\n}\n"
                "func backend_type_for_join_code(code: String) -> NetworkBackendType:\n"
                "\treturn NetworkBackendType.Relay\n"
                "\nfunc next_function() -> void:\n\tpass\n",
                encoding="utf-8",
            )
            lobby = root / "scenes/lobby/scripts/lobby_scene.gd"
            lobby.parent.mkdir(parents=True)
            lobby.write_text(
                "if GameManager.lan_multiplayer_enabled:\n"
                "\tNetworkManager.active_backend_type = NetworkManager.NetworkBackendType.Lan\n"
                "else:\n"
                "\tNetworkManager.active_backend_type = NetworkManager.NetworkBackendType.Relay\n",
                encoding="utf-8",
            )
            result = force_lan_only(root)
            manager_text = manager.read_text(encoding="utf-8")
            lobby_text = lobby.read_text(encoding="utf-8")
            self.assertIn("return NetworkBackendType.Lan", manager_text)
            self.assertNotIn("return NetworkBackendType.Relay", manager_text)
            self.assertIn("enum NetworkBackendType{\n\tLan\n}", manager_text)
            self.assertNotIn("NetworkBackendType.Steam", manager_text)
            self.assertNotIn("NetworkBackendType.Online", manager_text)
            self.assertNotIn("NetworkBackendType.Relay", manager_text)
            self.assertIn("active_backend_type = NetworkManager.NetworkBackendType.Lan", lobby_text)
            self.assertNotIn("NetworkBackendType.Relay", lobby_text)
            self.assertEqual(result["network_mode"], "LAN_ONLY")

if __name__ == "__main__":
    unittest.main()
