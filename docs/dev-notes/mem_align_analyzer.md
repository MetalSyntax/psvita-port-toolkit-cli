# `mem_align_analyzer.py` — Developer Notes

## Why this stays a disassembly-level regex scan, not real data-flow/value-set analysis

Proving a specific `ldrd`/`vld1`/... instruction WILL be fed an unaligned address at runtime
needs value-set analysis over the actual register/stack state at that point -- a static-analysis
project of its own, well beyond what a porting toolkit should own. What's genuinely useful
without that: knowing WHICH functions in the binary use these Cortex-A9-strict instructions at
all, since on Android's compiler defaults the source-level reason for the address happening to be
misaligned is almost always visible a few lines away in the Ghidra pseudo-C (a `void*`-cast
network/file buffer, a struct with mixed field widths). This module gets the porter to that
short list fast; it was never going to be able to prove which entries are real crashes.

## Why the mnemonic list is `ldrd`/`strd`/`vld1`/`vst1`/`vldm`/`vstm` and not every load/store

Plain `ldr`/`str`/`ldrb`/`strb`/... are explicitly NOT flagged: Cortex-A9 (like the Android
targets these binaries were built for) only traps on those when `SCTLR.A` is set, and this
toolkit has no way to know the loader's own `SCTLR` configuration, so flagging every ordinary
load/store would bury the genuinely-always-checked instructions in noise. `ldrd`/`strd` and the
NEON/VFP `vld1`/`vst1`/`vldm`/`vstm` multiple-register transfers are checked unconditionally on
this core regardless of that bit, which is what makes them worth a dedicated pass.

## Why `arm-vita-eabi-objdump` disassembles an Android-target `.so` fine

The mnemonics above are pure ARM/Thumb-2 instruction encoding, unrelated to which OS/ABI the ELF
was linked against. `utils.search_symbols()` already relies on the same VITASDK toolchain's
`readelf` for exactly this reason (a stripped binary's *symbol table* can differ across
toolchains, but its *machine code* doesn't) -- this module reuses that same PATH-setup convention
rather than requiring the porter to separately install an Android NDK toolchain just for this scan.

## Why struct-packing detection stays a narrow two-pattern regex, not a real parser

`#pragma pack`/`__attribute__((packed))` are unambiguous declarations worth flagging outright.
The second pattern (a `memcpy`/`recv`/`read`/`fread` call followed within a few lines by a
struct-pointer cast) is deliberately loose -- it's a "go look at this" pointer into Ghidra's
already-imperfect pseudo-C, not a claim that a bug exists there. A real parser would need to
track variable provenance and Ghidra's structure recovery model, both fragile against decompiler
version drift; the shallow regex degrades gracefully (misses things, doesn't fabricate results)
instead of confidently misreporting.
