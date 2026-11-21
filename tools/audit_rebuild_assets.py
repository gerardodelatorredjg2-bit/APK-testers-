#!/usr/bin/env python3
"""Non-destructive Godot asset inventory.

Usage: python3 tools/audit_rebuild_assets.py /path/to/project [--top 80]
Writes asset_inventory.csv and asset_inventory_summary.md into the project.
"""
from __future__ import annotations
import argparse, csv, hashlib
from pathlib import Path
from collections import Counter, defaultdict

SKIP_DIRS = {".git", ".godot", "__pycache__"}

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--top", type=int, default=80)
    args = ap.parse_args()
    root = args.project.resolve()
    if not (root / "project.godot").is_file():
        raise SystemExit(f"project.godot missing: {root}")
    files = [p for p in root.rglob("*") if p.is_file() and not any(part in SKIP_DIRS for part in p.relative_to(root).parts)]
    rows, types, bytes_by_type, duplicates = [], Counter(), Counter(), defaultdict(list)
    for p in files:
        rel, size, ext = p.relative_to(root).as_posix(), p.stat().st_size, p.suffix.lower() or "[none]"
        digest = sha256(p)
        rows.append({"path":rel,"extension":ext,"bytes":size,"sha256":digest})
        types[ext] += 1; bytes_by_type[ext] += size; duplicates[digest].append((rel,size))
    rows.sort(key=lambda x:x["bytes"], reverse=True)
    with (root/"asset_inventory.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["path","extension","bytes","sha256"]); w.writeheader(); w.writerows(rows)
    dupsets=[g for g in duplicates.values() if len(g)>1]
    dupbytes=sum(g[0][1]*(len(g)-1) for g in dupsets)
    total=sum(x["bytes"] for x in rows)
    lines=["# Godot resource inventory","",f"- Files: {len(rows)}",f"- Total: {total:,} bytes ({total/1048576:.1f} MiB)",f"- Duplicate sets: {len(dupsets)}",f"- Duplicate overhead: {dupbytes:,} bytes ({dupbytes/1048576:.1f} MiB)","","## By file type","","| Extension | Files | MiB |","|---|---:|---:|"]
    for ext,count in types.most_common(): lines.append(f"| {ext} | {count} | {bytes_by_type[ext]/1048576:.2f} |")
    lines += ["",f"## Largest {min(max(0,args.top),len(rows))} files","","| File | MiB | Type |","|---|---:|---|"]
    for r in rows[:max(0,args.top)]: lines.append(f"| `{r['path']}` | {r['bytes']/1048576:.2f} | {r['extension']} |")
    lines += ["","## Byte-identical duplicates",""]
    if dupsets:
        for g in sorted(dupsets,key=lambda x:x[0][1],reverse=True): lines.append(f"- {g[0][1]:,} bytes each: "+", ".join(f"`{p}`" for p,_ in g))
    else: lines.append("None found.")
    (root/"asset_inventory_summary.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(f"Inventoried {len(rows)} files, {total/1048576:.1f} MiB; exact duplicate overhead {dupbytes/1048576:.1f} MiB")
if __name__=="__main__": main()
