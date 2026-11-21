import tempfile
import unittest
from pathlib import Path
from tools.prepare_lean_project import prepare_project

class PrepareLeanProjectTests(unittest.TestCase):
    def test_removes_optional_extensions_and_gate_but_keeps_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "addons/audio_gate").mkdir(parents=True)
            (p / "addons/godotsteam").mkdir(parents=True)
            (p / "addons/steam-multiplayer-peer").mkdir(parents=True)
            for rel in (
                "addons/audio_gate/audio_gate.gdextension",
                "addons/godotsteam/godotsteam.gdextension",
                "addons/steam-multiplayer-peer/steam-multiplayer-peer.gdextension",
            ):
                (p / rel).write_text("[configuration]\n")
            (p / "project.godot").write_text('[rendering]\nrenderer/rendering_method="mobile"\n')
            bus = '''[gd_resource type="AudioBusLayout" load_steps=4 format=3]
[sub_resource type="AudioEffectCapture" id="Capture"]
[sub_resource type="AudioEffectGate" id="Gate"]
resource_name = "Gate"
[resource]
bus/1/effect/0/effect = SubResource("Capture")
bus/1/effect/1/effect = SubResource("Gate")
bus/1/effect/1/enabled = true
'''
            (p / "bus_layout.tres").write_text(bus)
            (p / "default_bus_layout.tres").write_text(bus)
            src = p / "modules/example.gd"
            src.parent.mkdir()
            src.write_text('const X = preload("uid://bwixccr50fbc2")\n')
            report = prepare_project(p)
            self.assertEqual(len(report["removed_optional_extensions"]), 3)
            self.assertEqual(len(report["bus_layouts_without_native_gate"]), 2)
            self.assertIn("AudioEffectCapture", (p / "bus_layout.tres").read_text())
            self.assertIn('bus/1/effect/0/enabled = true', (p / "bus_layout.tres").read_text())
            self.assertIn('bus/1/effect/0/enabled = true', (p / "default_bus_layout.tres").read_text())
            self.assertNotIn("AudioEffectGate", (p / "bus_layout.tres").read_text())
            self.assertIn("intermission_control_input_label.tscn", src.read_text())
            self.assertIn('renderer/rendering_method.mobile="gl_compatibility"', (p / "project.godot").read_text())

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "project.godot").write_text("[rendering]\n")
            report1 = prepare_project(p)
            report2 = prepare_project(p)
            self.assertTrue(report1["android_compatibility_renderer_set"])
            self.assertFalse(report2["android_compatibility_renderer_set"])

if __name__ == "__main__":
    unittest.main()
