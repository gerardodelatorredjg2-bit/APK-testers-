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


if __name__ == "__main__":
    unittest.main(verbosity=2)
