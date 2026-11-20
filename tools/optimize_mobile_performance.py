from __future__ import annotations
import argparse, json, os, re, sys
from pathlib import Path

PROFILES = {"low":{"scale":0.50,"fps":30,"max_lights":2},"balanced":{"scale":0.65,"fps":45,"max_lights":3},"high":{"scale":0.75,"fps":60,"max_lights":4}}
COMMON = {"rendering/anti_aliasing/quality/msaa_3d":"0","rendering/anti_aliasing/quality/screen_space_aa":"0","rendering/environment/ssao/quality":"0","rendering/environment/ssil/quality":"0","rendering/environment/screen_space_reflection/roughness_quality":"0"}

def set_project_setting(lines, section, key, value):
    header=f"[{section}]"
    starts=[i for i,line in enumerate(lines) if line.strip()==header]
    if not starts:
        if lines and lines[-1].strip(): lines.append("")
        lines.extend([header,f"{key}={value}"]); return
    ranges=[]
    for start in starts:
        end=next((i for i in range(start+1,len(lines)) if lines[i].strip().startswith("[") and lines[i].strip().endswith("]")),len(lines))
        ranges.append((start,end))
    pattern=re.compile(r"^\\s*"+re.escape(key)+r"\\s*=")
    matches=[i for start,end in ranges for i in range(start+1,end) if pattern.match(lines[i])]
    if matches:
        lines[matches[0]]=f"{key}={value}"
        for duplicate in reversed(matches[1:]): del lines[duplicate]
    else:
        start=ranges[0][0]
        end=next((i for i in range(start+1,len(lines)) if lines[i].strip().startswith("[") and lines[i].strip().endswith("]")),len(lines))
        lines.insert(end,f"{key}={value}")

def replace_typed_assignment(source,name,value):
    pattern=re.compile(rf"(?m)^(\\s*(?:@export\\s+)?var\\s+{re.escape(name)}(?:\\s*:\\s*[^=]+)?\\s*=\\s*)[0-9]+(?:\\.[0-9]+)?(\\s*(?:#.*)?)$")
    return pattern.subn(rf"\\g<1>{value}\\g<2>",source)

def apply(root,profile_name):
    if profile_name not in PROFILES: raise ValueError(f"Unknown profile {profile_name}; choose low, balanced, high")
    project=root/"project.godot"
    if not project.is_file(): raise FileNotFoundError(f"project.godot missing: {project}")
    profile=PROFILES[profile_name]; scale=float(profile["scale"]); fps=int(profile["fps"]); lights=int(profile["max_lights"])
    settings=dict(COMMON); settings.update({"rendering/scaling_3d/scale":str(scale),"application/run/max_fps":str(fps)})
    lines=project.read_text(encoding="utf-8").splitlines()
    for key,value in settings.items():
        section,setting=key.split("/",1); set_project_setting(lines,section,setting,value)
    project.write_text("\n".join(lines)+"\n",encoding="utf-8")
    changed=[]
    allowed={"mobilegamesettings","mobileboost","mobilelightoptimizer","allinonemobileoptimizer"}
    for path in root.rglob("*.gd"):
        if ".godot" in path.parts or path.stem.lower() not in allowed: continue
        source=path.read_text(encoding="utf-8"); updated=source
        for name,val in (("scaling_3d_scale",scale),("target_mobile_fps",fps),("max_lights",lights)):
            updated,_=replace_typed_assignment(updated,name,val)
        if updated!=source: path.write_text(updated,encoding="utf-8"); changed.append(str(path.relative_to(root)))
    report={"profile":profile_name,"render_scale":scale,"fps_cap":fps,"max_dynamic_lights":lights,"disabled_quality_features":["MSAA 3D","screen-space AA","SSAO","SSIL","SSR quality"],"runtime_optimizer_files_changed":sorted(changed),"note":"Static profile, not dynamic runtime adaptation."}
    report_path=Path(os.environ.get("MOBILE_PERFORMANCE_REPORT",root.parent/"mobile-performance-report.json")); report_path.parent.mkdir(parents=True,exist_ok=True); report_path.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    return report

def main():
    parser=argparse.ArgumentParser(description="Apply a mobile rendering/performance profile.")
    parser.add_argument("project",type=Path); parser.add_argument("--profile",choices=sorted(PROFILES),default=os.environ.get("MOBILE_PROFILE","low")); args=parser.parse_args()
    try: print(json.dumps(apply(args.project.resolve(),args.profile),indent=2))
    except (OSError,ValueError) as exc: print(f"ERROR: {exc}",file=sys.stderr); return 2
    return 0
if __name__=="__main__": raise SystemExit(main())
