# `perf_telemetry.py` — Developer Notes

## Why there's no GPU (PowerVR SGX543MP4+) counter, despite the plan asking for one

Vertex counts and rasterization-rate counters aren't exposed to homebrew through any public
vitasdk API discoverable for this project. Fabricating a plausible-looking number (or silently
reusing frame time and CALLING it a GPU metric) would be exactly the kind of dishonest "drop-in"
this project's other modules (`so_patcher.py`, `mem_profiler.py`) deliberately avoid. Frame time
itself is left as the headline metric instead -- not a consolation prize: a GPU-bound frame
shows up as a long frame time regardless of whether a dedicated counter exists behind it, so for
the stated goal ("reach a stable 60 FPS") it's arguably the more directly actionable number
anyway.

## Why the per-core sampler is explicitly marked "verify against your headers", unlike frame timing

`sceKernelGetProcessTimeWide()` (frame timing) is a simple, stable, long-documented call with no
ambiguity. `sceKernelGetThreadRunStatus()`'s `SceKernelThreadRunStatus`/`cpuInfo[]` struct layout
is more exposed to header-version drift across vitasdk releases -- getting a field name wrong
there is a compile error, not a silently wrong result, which is the safer failure mode, but it's
still not something this toolkit can assert as verified-correct for every vitasdk version a
porter might have installed. The generated comment says exactly that, and says the porter can
delete the whole function if it doesn't compile -- frame-time telemetry works completely
independently of it.

## Why frame-pacing analysis (p95, stutter count) uses a fixed 2x-average threshold

A stutter is fundamentally "this frame took much longer than its neighbors", and the game's own
target frame time varies (30fps vs 60fps titles, or a deliberately heavier boss-fight frame).
2x the RECENT rolling average scales with whatever the game's own actual pacing already is,
rather than hardcoding an absolute millisecond value that would misclassify a 30fps-target game
as "stuttering constantly". It's a heuristic, not a certified detector -- consistent with how
this toolkit treats every other predictive/heuristic report (`mem_align_analyzer.py`'s alignment
risks, `mem_profiler.py`'s leak candidates).
