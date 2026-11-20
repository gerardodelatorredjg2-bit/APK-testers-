from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("apply_runtime_cleanliness_fixes.py")
spec = importlib.util.spec_from_file_location("runtime_cleanliness", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class RuntimeCleanlinessFixTests(unittest.TestCase):
    def test_menu_anchor_fix_uses_offsets_not_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            menu = project / module.MENU_RELATIVE
            menu.parent.mkdir(parents=True)
            menu.write_text(
                "func check_if_locale_requires_vertical_adjustment():\n"
                "\tif true:\n\t\tmargin_container.size = size_required\n"
                "\telse:\n\t\tmargin_container.size = size_default\n"
                "func show_post_original():\n\tpass\n",
                encoding="utf-8",
            )
            self.assertTrue(module.patch_menu_visual_manager(project))
            patched = menu.read_text(encoding="utf-8")
            self.assertNotIn("margin_container.size =", patched)
            self.assertIn("margin_container.offset_right =", patched)
            self.assertIn("margin_container.offset_bottom =", patched)
            self.assertFalse(module.patch_menu_visual_manager(project))

    def test_disable_mobile_ssao_is_recursive_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            nested = project / "minigames" / "test"
            nested.mkdir(parents=True)
            scene = nested / "test.tscn"
            scene.write_text("ssao_enabled = true\nssao_radius = 0.5\n", encoding="utf-8")
            resource = project / "world.tres"
            resource.write_text("ssao_enabled = true # mobile unsupported\n", encoding="utf-8")
            self.assertEqual(module.disable_unsupported_ssao(project), 2)
            self.assertIn("ssao_enabled = false", scene.read_text(encoding="utf-8"))
            self.assertIn("ssao_enabled = false # mobile unsupported", resource.read_text(encoding="utf-8"))
            self.assertEqual(module.disable_unsupported_ssao(project), 0)

    @staticmethod
    def _ogg_page(packet: bytes, sequence: int, serial: int = 0x4D505459) -> bytes:
        lacing = []
        remaining = len(packet)
        while remaining >= 255:
            lacing.append(255)
            remaining -= 255
        lacing.append(remaining)
        header = (
            b"OggS" + b"\x00" + b"\x00" + (0).to_bytes(8, "little", signed=True)
            + serial.to_bytes(4, "little") + sequence.to_bytes(4, "little")
            + b"\x00\x00\x00\x00" + bytes([len(lacing)]) + bytes(lacing)
        )
        page = bytearray(header + packet)
        page[22:26] = module.struct.pack("<I", module._ogg_crc32(page))
        return bytes(page)

    @staticmethod
    def _packets(data: bytes):
        packets = []
        offset = 0
        current = bytearray()
        while offset < len(data):
            assert data[offset:offset + 4] == b"OggS"
            segment_count = data[offset + 26]
            table_end = offset + 27 + segment_count
            lacing = data[offset + 27:table_end]
            body_end = table_end + sum(lacing)
            body_position = table_end
            for length in lacing:
                current.extend(data[body_position:body_position + length])
                body_position += length
                if length < 255:
                    packets.append(bytes(current))
                    current.clear()
            offset = body_end
        return packets

    def test_malformed_vorbis_comment_repair_preserves_audio_packets_and_page_size(self):
        identification = b"\x01vorbis" + bytes(range(23))
        vendor = b"Original Encoder"
        malformed = b"NO_SEPARATOR_TAG"
        comment = (
            b"\x03vorbis" + module.struct.pack("<I", len(vendor)) + vendor
            + module.struct.pack("<I", 1) + module.struct.pack("<I", len(malformed))
            + malformed + b"\x01"
        )
        setup = b"\x05vorbis" + b"unchanged setup packet"
        audio = b"\x00" + bytes(range(64))
        original = b"".join([
            self._ogg_page(identification, 0),
            self._ogg_page(comment, 1),
            self._ogg_page(setup, 2),
            self._ogg_page(audio, 3),
        ])

        cleaned, changed = module._sanitize_ogg_comment_bytes(original)
        self.assertTrue(changed)
        self.assertEqual(len(cleaned), len(original))
        before_packets = self._packets(original)
        after_packets = self._packets(cleaned)
        self.assertEqual(len(before_packets), 4)
        self.assertEqual(after_packets[0], before_packets[0])
        self.assertEqual(len(after_packets[1]), len(before_packets[1]))
        self.assertTrue(module._valid_vorbis_comment_packet(after_packets[1]))
        self.assertEqual(after_packets[2], before_packets[2])
        self.assertEqual(after_packets[3], before_packets[3])

        offset = 0
        while offset < len(cleaned):
            segment_count = cleaned[offset + 26]
            page_end = offset + 27 + segment_count + sum(cleaned[offset + 27:offset + 27 + segment_count])
            page = bytearray(cleaned[offset:page_end])
            expected_crc = int.from_bytes(page[22:26], "little")
            page[22:26] = b"\x00\x00\x00\x00"
            self.assertEqual(module._ogg_crc32(page), expected_crc)
            offset = page_end

    def test_valid_lowercase_vorbis_tags_are_not_modified(self):
        vendor = b"Test encoder"
        fields = [b"title=valid", b"artist-alt=valid"]
        packet = (
            b"\x03vorbis" + module.struct.pack("<I", len(vendor)) + vendor
            + module.struct.pack("<I", len(fields))
            + b"".join(module.struct.pack("<I", len(field)) + field for field in fields)
            + b"\x01"
        )
        self.assertTrue(module._valid_vorbis_comment_packet(packet))
        original = b"".join([
            self._ogg_page(b"\x01vorbis" + bytes(range(23)), 0),
            self._ogg_page(packet, 1),
            self._ogg_page(b"\x05vorbissetup", 2),
            self._ogg_page(b"\x00audio packet", 3),
        ])
        cleaned, changed = module._sanitize_ogg_comment_bytes(original)
        self.assertFalse(changed)
        self.assertEqual(cleaned, original)

    def test_mobile_compatibility_shader_does_not_use_fma(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            shader = project / "shaders" / "spatial" / "grass_sway.gdshader"
            shader.parent.mkdir(parents=True)
            shader.write_text("float t = sin(fma(TIME + COLOR.r, 2.0, COLOR.g));\n", encoding="utf-8")
            self.assertTrue(module.patch_compatibility_shader(project))
            patched = shader.read_text(encoding="utf-8")
            self.assertIn("sin((TIME + COLOR.r) * 2.0 + COLOR.g)", patched)
            self.assertNotIn("fma(", patched)
            self.assertFalse(module.patch_compatibility_shader(project))

    def test_stale_shared_texture_uid_is_removed_from_all_scenes(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            pause_scene = project / "modules" / "pause_menu" / "pause_menu.tscn"
            pause_scene.parent.mkdir(parents=True)
            pause_scene.write_text(
                '[ext_resource type="Texture2D" uid="uid://stale" path="res://textures/ui/white_border_ninepatch.png" id="9"]\n'
                '[ext_resource type="Script" uid="uid://valid" path="res://other.gd" id="1"]\n',
                encoding="utf-8",
            )
            lobby_scene = project / "scenes" / "lobby" / "lobby_scene.tscn"
            lobby_scene.parent.mkdir(parents=True)
            lobby_scene.write_text(
                '[ext_resource type="Texture2D" uid="uid://stale" path="res://textures/ui/white_border_ninepatch.png" id="54"]\n',
                encoding="utf-8",
            )
            self.assertTrue(module.patch_pause_menu_uid(project))
            self.assertNotIn('uid="uid://stale"', pause_scene.read_text(encoding="utf-8"))
            self.assertNotIn('uid="uid://stale"', lobby_scene.read_text(encoding="utf-8"))
            self.assertIn('uid="uid://valid"', pause_scene.read_text(encoding="utf-8"))
            self.assertFalse(module.patch_pause_menu_uid(project))

    def test_effect_manager_disables_compatibility_particle_trails(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            manager = project / "modules" / "effect_manager" / "effect_manager.gd"
            manager.parent.mkdir(parents=True)
            manager.write_text(
                "func _ready():\n\tinitialize()\n\tprecache()\n"
                "\t\tvar effect_instance = effect.instantiate()\n\t\tinstanced.append(effect_instance)\n"
                "\tvar instance = prefab_scenes[effect_name].instantiate()\n\tspawn_node.add_child(instance, true)\n",
                encoding="utf-8",
            )
            self.assertTrue(module.patch_effect_manager_for_compatibility(project))
            patched = manager.read_text(encoding="utf-8")
            self.assertIn('get_current_rendering_method() == "gl_compatibility"', patched)
            self.assertIn("particles.trail_enabled = false", patched)
            self.assertIn("_disable_compatibility_particle_trails(effect_instance)", patched)
            self.assertIn("_disable_compatibility_particle_trails(instance)", patched)
            self.assertFalse(module.patch_effect_manager_for_compatibility(project))


if __name__ == "__main__":
    unittest.main(verbosity=2)
