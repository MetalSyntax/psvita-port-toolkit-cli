# `mem_profiler.py` — Developer Notes

## Why this doesn't hook the game's `.so` allocator calls automatically

Same limitation `so_patcher.py` already documents for binary patching: this toolkit has no ELF
loader/relocator of its own, so it can't rewrite the game `.so`'s import table to point
`malloc`/`free`/... at instrumented wrappers by itself. What it CAN do honestly is generate those
wrappers (`mp_malloc`/`mp_free`/`mp_calloc`/`mp_realloc`) as reviewable source, and point the
porter at the one thing every soloader-based port already has: a table resolving the game
binary's libc imports (the same mechanism a soloader uses to satisfy the game's calls into a
fake `libc`/JNI environment). Wiring these four function pointers into that existing table
instead of the real allocator entries is a few-line change the porter makes once, not something
this toolkit can safely do without knowing that table's exact shape in their specific loader.

## Why the leak heuristic is "checkpoint-relative", not a fixed time threshold

A fixed "still alive after N seconds" threshold has no good universal value: a texture atlas
legitimately lives for an entire level, while a per-frame scratch buffer should die in
milliseconds. Level/scene transitions are the natural point at which a game's OWN allocations
are expected to have been cleaned up, so this listens for an explicit `CHECKPOINT` event (the
porter calls `mem_profiler_checkpoint("level2_start")` once at each transition they care about)
and reports whatever's still alive from BEFORE the most recent one as the leak candidate list.
A project that never sends a checkpoint still gets accurate live-byte/live-block counts -- it
just doesn't get the leak-vs-still-in-use split, which is an honest degradation rather than a
made-up threshold.

## Why the wire format is CSV text, not a binary struct

Same reasoning as `debugnet_server.py`'s plain-text-line convention: there's no existing ground
truth in this port collection for a binary metrics protocol to match, and a human can read a raw
UDP capture of `ALLOC,0x1a2b3c4,128,` on a hunch without needing this toolkit's source open next
to it. The fixed 4-field shape (`event,ptr,size,tag`) keeps a malformed/truncated datagram easy
to detect and skip (`_parse_event()` returns `None`) rather than silently misparsing into the
wrong field.

## Why the default port is 9998, not 9999

`9999` is `debugnet_server.DEFAULT_PORT` (live text logs) -- a second, independent UDP stream for
structured heap events needs its own port so both servers can run side by side without the
porter having to change either one's default just to use them together.
