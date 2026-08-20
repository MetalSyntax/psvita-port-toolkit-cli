# `so_patcher.py` — Developer Notes

## Why detection runs two passes (`.so` bytes + decompiled Java) instead of one

A telemetry/IAP SDK's characteristic strings (package names, class names) are primarily
Java/DEX-shaped. The game's OWN native `.so` often won't contain them at all even when the APK
clearly bundles the SDK, because the Java glue calls into the SDK's OWN separate `.so`
(`libgms.so`-style), not the game's. Scanning only the game's `.so` would silently miss most real
integrations; scanning only the decompiled Java misses the (less common but real) case of a SDK
statically linked directly into the game's own native code. Both passes run every time, and
`document_findings_in_plan()` records which source(s) each hit came from.

## Why `generate_telemetry_stubs()` writes checklists, not fabricated function bodies

Static detection confirms an SDK is PRESENT, not its exact native entry-point signatures --
those depend on the specific SDK version and how the game's build wired it in, neither of which
this scan can see without the SDK's own headers. A plausible-looking
`FirebaseAnalytics_logEvent(...)` stub calling entry points nobody confirmed exist on the real
call path would be worse than no stub at all: it would look done while silently being wrong.
The checklists say exactly what a porter needs to verify by reading the real Java (jadx has it)
before wiring anything in -- same posture as `jni_analyzer.generate_jni_stubs()`.

## Why the binary patcher edits the `.so` FILE, not the loaded process's memory

"Patch a `.so` at runtime, in memory" and "patch the `.so` file before it's ever loaded" sound
similar but need completely different engineering: the first needs cooperation from whatever
loader/relocator maps that file into the console's RAM (an API this toolkit doesn't own, and
can't assume any specific soloader implements the same way). The second needs nothing but ELF
file-format math -- the loader just reads whatever bytes are already on disk, so editing those
bytes ahead of time produces the exact same runtime effect without requiring any loader
cooperation at all. `apply_binary_patch()` does the second thing. It was originally declined
entirely (the module's first version treated ALL in-memory-shaped patching as out of scope); the
file-level distinction is what makes a real implementation possible without overclaiming control
this toolkit doesn't have.

## Why the ELF parsing is hand-written instead of shelling out to `readelf`

`vaddr_to_file_offset()` only needs three small, extremely stable parts of the ELF32 spec (the
header's `e_phoff`/`e_phentsize`/`e_phnum` fields, and each `Elf32_Phdr`'s `p_type`/`p_offset`/
`p_vaddr`/`p_filesz`) -- parsing those ~40 bytes directly with `struct.unpack_from()` is both
simpler and more reliably stable across machines than depending on some installed `readelf`
binary's text output format (which varies by binutils version and isn't a stable interface to
parse against). This also means the patcher works even on a machine with no VITASDK/binutils
toolchain configured at all -- it never shells out to anything.

## Why this still can't tell the porter WHICH address to patch

Confirming that a specific virtual address is actually a safe, correct patch point (a real
function entry, not the middle of an instruction; the right function, not a coincidentally
similar one; a genuinely blocking/telemetry call, not something load-bearing) needs cross-
referencing against Ghidra's decompiled pseudo-C and/or `crash_analyzer.py`'s resolved symbols --
exactly the kind of judgment call this project consistently leaves to the porter rather than
automating with unverified confidence (same posture as `mem_align_analyzer.py`'s "heuristic,
predictive" alignment report). `apply_binary_patch()` makes APPLYING a confirmed patch
mechanical and safe (backed up, logged, revertible); it deliberately does not try to guess the
address itself.

## Why every patch is backed up before being written, and the backup is never overwritten

`.so.orig` is only ever created if it doesn't already exist. Without that check, applying a
second patch later would overwrite the true original with an already-patched copy, silently
destroying the ability to fully revert. `revert_binary_patches()` restores from that one
preserved original and deletes the patch log, so a re-patch after a revert starts from a clean
slate rather than compounding on top of a stale log.
