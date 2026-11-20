# Machine Party Android performance audit — evidence and scope

## Confirmed findings from the available validation artifact

- Godot editor import completed without GDScript parse errors in the recovery validation run.
- Headless main-scene smoke test passed.
- The runtime smoke log still reports leaked RID/resource allocations at shutdown (2 Shape3D, 54 dummy textures, 31 dummy meshes, 36 dummy materials, 16 dummy shaders; 191 resources in use). Headless/dummy-renderer shutdown reports can include test-environment artifacts; these are not proof of a gameplay memory leak, but they warrant profiling on-device.
- Editor import log shows repeated `Invalid comment in Ogg Vorbis file` warnings for several audio assets.
- A Control anchor warning indicates a node's size will be overridden after `_ready()`; inspect the specific node in the scene when source project is available.
- The previous mobile optimizer used broad regex substitutions, forced a single 50% 3D scale and 30-FPS target, and did not deduplicate repeated settings across duplicate section headers. That is now replaced with targeted typed-variable matching and idempotent project-setting updates.

## Changes in this PR

- Static profiles: low (0.50 scale / 30 FPS / 2 lights), balanced (0.65 / 45 / 3), high (0.75 / 60 / 4).
- Target only known mobile optimizer scripts and ignore generated .godot cache.
- Produce a JSON report of applied profile.
- Run 5 unit tests in CI before attempting Android build.

## Important limitations / next evidence needed

- These profiles are build-time settings, not runtime adaptive quality.
- Resolution scaling and FPS caps are tradeoffs and need measured GPU frame time, CPU frame time, thermals, and battery on low-, mid-, and high-end physical phones.
- The full recovered Godot project is in a 1.86-GB workflow artifact; the available artifact downloader has a 512-MB limit. Full scene/script-level profiling and edits are therefore not possible in this environment.
- No claim is made that every minigame, online/LAN session, scene transition, or device has been tested.
- Next high-value targets once full source is accessible: profile the light optimizer's recursive scene-tree scan/sorting cost; check repeated per-frame allocations and signal/timer leaks; inspect minigame-specific physics and process loops; audit large texture/audio imports; replace problematic Ogg metadata safely; test scene preloading/freeing and Android lifecycle/resume; run LAN latency/packet-loss tests; and validate Vulkan/Compatibility rendering and memory behavior on physical devices.

## Suggested device test matrix

- Low end: 2 GB RAM class, older GPU, Android 8/9-class device if supported.
- Mid-range: 4–6 GB RAM device, 60-Hz display.
- High end: recent device, 90/120-Hz display.

Measure frame-time percentiles (not only average FPS), memory peak, startup/loading times, temperature after 15–30 minutes, and crashes during menu → lobby → minigame → results → next minigame loops.
