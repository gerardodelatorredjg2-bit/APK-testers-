import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parent / "optimize_mobile_performance.py"
spec = importlib.util.spec_from_file_location("mobile_optimizer", MODULE_PATH)
optimizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(optimizer)


class MobileOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        (self.root / "project.godot").write_text(
            "[application]\nrun/max_fps=120\n\n[rendering]\nscaling_3d/scale=1.0\n",
            encoding="utf-8",
        )
        (self.root / "AllInOneMobileOptimizer.gd").write_text(
            "extends Node\n@export var target_mobile_fps: int = 60\n@export var max_lights: int = 8\n",
            encoding="utf-8",
        )
        (self.root / "MobileBoost.gd").write_text(
            "extends Node\n@export var scaling_3d_scale: float = 0.8\n",
            encoding="utf-8",
        )
        (self.root / ".godot").mkdir()
        (self.root / ".godot" / "AllInOneMobileOptimizer.gd").write_text(
            "target_mobile_fps = 99\n", encoding="utf-8"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_low_profile_applies_expected_defaults_and_report(self):
        report = optimizer.apply(self.root, "low")
        config = (self.root / "project.godot").read_text(encoding="utf-8")
        runtime = (self.root / "AllInOneMobileOptimizer.gd").read_text(encoding="utf-8")
        boost = (self.root / "MobileBoost.gd").read_text(encoding="utf-8")
        self.assertIn("run/max_fps=30", config)
        self.assertIn("scaling_3d/scale=0.5", config)
        self.assertIn("target_mobile_fps: int = 30", runtime)
        self.assertIn("max_lights: int = 2", runtime)
        self.assertIn("scaling_3d_scale: float = 0.5", boost)
        self.assertEqual(report["profile"], "low")
        saved = json.loads((self.root.parent / "mobile-performance-report.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["fps_cap"], 30)

    def test_balanced_and_high_profiles_are_distinct(self):
        balanced = optimizer.apply(self.root, "balanced")
        high = optimizer.apply(self.root, "high")
        self.assertEqual(
            (balanced["render_scale"], balanced["fps_cap"], balanced["max_dynamic_lights"]),
            (0.65, 45, 3),
        )
        self.assertEqual(
            (high["render_scale"], high["fps_cap"], high["max_dynamic_lights"]),
            (0.75, 60, 4),
        )

    def test_repeated_application_is_idempotent_and_deduplicates_config(self):
        config_path = self.root / "project.godot"
        original = config_path.read_text(encoding="utf-8")
        config_path.write_text(original + "\n[rendering]\nscaling_3d/scale=0.3\n", encoding="utf-8")
        optimizer.apply(self.root, "low")
        first = config_path.read_text(encoding="utf-8")
        optimizer.apply(self.root, "low")
        second = config_path.read_text(encoding="utf-8")
        self.assertEqual(first, second)
        self.assertEqual(first.count("scaling_3d/scale="), 1)

    def test_dry_run_reports_changes_without_writing(self):
        config_path = self.root / "project.godot"
        original = config_path.read_text(encoding="utf-8")
        report = optimizer.apply(self.root, "low", dry_run=True)
        self.assertTrue(report["dry_run"])
        self.assertIn("project.godot", report["files_to_write"])
        self.assertEqual(config_path.read_text(encoding="utf-8"), original)
        self.assertFalse((self.root.parent / "mobile-performance-report.json").exists())

    def test_invalid_profile_and_missing_project_fail(self):
        with self.assertRaises(ValueError):
            optimizer.apply(self.root, "ultra")
        with self.assertRaises(FileNotFoundError):
            optimizer.apply(self.root / "missing", "low")

    def test_optimizer_files_are_targeted_and_cache_is_ignored(self):
        unrelated = self.root / "unrelated.gd"
        unrelated.write_text("@export var max_lights: int = 10\n", encoding="utf-8")
        optimizer.apply(self.root, "balanced")
        runtime = (self.root / "AllInOneMobileOptimizer.gd").read_text(encoding="utf-8")
        self.assertIn("max_lights: int = 3", runtime)
        self.assertIn("max_lights: int = 10", unrelated.read_text(encoding="utf-8"))
        self.assertIn("99", (self.root / ".godot" / "AllInOneMobileOptimizer.gd").read_text(encoding="utf-8"))

    def test_high_poly_static_environment_imports_enable_lod_safely(self):
        relative = optimizer.STATIC_ENVIRONMENT_LOD_IMPORTS[0]
        import_path = self.root / relative
        import_path.parent.mkdir(parents=True, exist_ok=True)
        original = (
            '[remap]\\nimporter="scene"\\n\\n'
            '[params]\\nmeshes/generate_lods=false\\nmeshes/create_shadow_meshes=true\\n'
        )
        import_path.write_text(original, encoding="utf-8")

        report = optimizer.apply(self.root, "low")
        updated = import_path.read_text(encoding="utf-8")
        self.assertIn("meshes/generate_lods=true", updated)
        self.assertIn("meshes/create_shadow_meshes=true", updated)
        self.assertIn(relative, report["static_environment_lod_imports_enabled"])
        self.assertIn(relative, report["files_to_write"])

        second = optimizer.apply(self.root, "low")
        self.assertEqual(second["static_environment_lod_imports_enabled"], [])


if __name__ == "__main__":
    unittest.main()
