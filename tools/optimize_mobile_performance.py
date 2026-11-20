from pathlib import Path
import re
import sys

root = Path(sys.argv[1]).resolve()
project = root / "project.godot"
if not project.is_file():
    raise SystemExit("project.godot not found")

# Apply conservative mobile defaults that reduce GPU cost without deleting content.
settings = {
    "rendering/scaling_3d/scale": "0.5",
    "rendering/anti_aliasing/quality/msaa_3d": "0",
    "rendering/anti_aliasing/quality/screen_space_aa": "0",
    "rendering/environment/ssao/quality": "0",
    "rendering/environment/ssil/quality": "0",
    "rendering/environment/screen_space_reflection/roughness_quality": "0",
    "application/run/max_fps": "30",
}
lines = project.read_text(encoding="utf-8").splitlines()

def set_project_setting(section: str, key: str, value: str) -> None:
    header = f"[{section}]"
    start = next((i for i, line in enumerate(lines) if line.strip() == header), -1)
    if start < 0:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([header, f"{key}={value}"])
        return
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[") and lines[i].strip().endswith("]")), len(lines))
    pattern = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
    matches = [i for i in range(start + 1, end) if pattern.match(lines[i])]
    if matches:
        lines[matches[-1]] = f"{key}={value}"
    else:
        lines.insert(end, f"{key}={value}")

for key, value in settings.items():
    section = key.split("/", 1)[0]
    set_project_setting(section, key, value)
project.write_text("\n".join(lines) + "\n", encoding="utf-8")

# Existing runtime optimizers can override project defaults. Force their mobile
# render scale to 50%, keep a stable 30 FPS target, and cap dynamic lights.
touched = []
for path in root.rglob("*.gd"):
    if ".godot" in path.parts:
        continue
    name = path.name.lower()
    if "mobilegamesettings" not in name and "allinonemobileoptimizer" not in name:
        continue
    source = path.read_text(encoding="utf-8")
    updated = re.sub(r"(scaling_3d_scale\s*=\s*)[0-9]+(?:\.[0-9]+)?", r"\g<1>0.5", source)
    updated = re.sub(r"(target_mobile_fps\s*=\s*)[0-9]+", r"\g<1>30", updated)
    updated = re.sub(r"(max_lights\s*=\s*)[0-9]+", r"\g<1>2", updated)
    if updated != source:
        path.write_text(updated, encoding="utf-8")
        touched.append(str(path.relative_to(root)))

print("Mobile performance defaults applied: 3D scale=50%, FPS cap=30, MSAA/SSAA/SSAO/SSIL/SSR quality disabled.")
print("Runtime optimizer files adjusted:", touched if touched else "none found or no matching overrides")
