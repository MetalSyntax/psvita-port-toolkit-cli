# `asset_transcoder.py` — Developer Notes

## Why `.rawtex` is this toolkit's own format, not a real Sony GXT

Sony's actual GXT binary layout isn't publicly documented, and GXM never actually requires it
for homebrew -- a raw linear pixel buffer loads fine via `sceGxmTextureInitLinear()` with an
explicit `SceGxmTextureFormat`. Claiming to produce "a GXT file" without a confirmed spec to
verify against would be indistinguishable, from the porter's side, from a genuinely broken
container that happens to have the right file extension. `.rawtex` is deliberately its own,
fully self-documented format (magic + per-mip offset table, written out in
`_write_rawtex()`'s docstring and mirrored in the `.json` sidecar) precisely so nothing about it
has to be taken on faith.

## Why texture compression (PVRTC/DXT) is a best-effort bonus, not the primary output

Writing a correct BC1/BC5/PVRTC encoder from scratch is real, error-prone codec work -- a subtly
wrong block-compression implementation produces textures that decode to visibly corrupted
garbage on real hardware, which is a correctness failure mode, not just a missing feature. Two
independently-downloadable, well-established tools (`PVRTexToolCLI` from Imagination
Technologies, `compressonatorcli` from AMD) already do this correctly; shelling out to whichever
one is actually installed is safer than reimplementing texture compression math inside this
toolkit. If neither is present, or the installed version's CLI flags don't match what this
module assumes, `_try_compressed_backend()` just returns `False` -- the always-correct `.rawtex`
output isn't affected either way. See `doctor.py`'s WARN-only check for the same reasoning.

## Why Android's own compressed texture formats (ETC1/ETC2/ASTC) aren't handled at all

Pillow -- this project's one hard dependency -- can't decode them, and there's no pure-Python
decoder for any of them. Silently skipping such a file with no explanation would look like a
bug; `transcode_texture_dir()` instead lets Pillow's own `Image.open()` failure surface per-file
(caught so one bad asset doesn't abort the batch), so the porter sees exactly which files need a
different tool (e.g. re-exporting from the original source art) rather than a mysterious gap.

## Why `generate_rawtex_loader()` exists at all

Every other generated-C module in this toolkit (`so_patcher.py`'s stubs, `mem_profiler.py`/
`perf_telemetry.py`'s hooks, `monkey_tester.py`'s hooks) pairs "here's a format/protocol" with
"here's real code that reads/writes it". `.rawtex` originally didn't have that second half --
just a documented byte layout with nothing in this repo actually loading it, which was an
inconsistency worth closing rather than leaving as a spec nobody implements. The generated loader
is held to the same "best-effort, verify against your headers" honesty as
`perf_telemetry.py`'s core sampler for the GXM-specific parts (exact `sceGxmTextureInitLinear()`
signature, `SCE_KERNEL_MEMBLOCK_TYPE_USER_RW_UNCACHE`) -- but the file-parsing half is asserted
correct without hedging, because it's just matching this module's OWN `_write_rawtex()` output,
verified directly against it.

## Why audio reuses `livearea._find_at9_encoder()` unchanged

That function already documents (and this module doesn't repeat) why there's no free ATRAC9
encoder to bundle -- VITASDK doesn't ship one, ffmpeg has none either, and a real encoder has to
come from the official Sony SDK toolset. Reusing the exact same lookup means both LiveArea's
`bgm.at9` and any batch-converted asset are held to one single, already-documented standard for
"is an encoder actually available", not two subtly different ones.
