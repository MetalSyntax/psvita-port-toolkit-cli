# `gdb_bridge.py` — Developer Notes

## Why this generates a client script instead of running a GDB server

This toolkit doesn't own the loader's code, so it can't guarantee any given soloader project
bundles a gdbstub, let alone which implementation. Claiming to run "a GDB server" would imply
control over something this project never has. What's genuinely useful without that assumption:
the mechanical, always-needed client-side setup -- pointing `gdb-multiarch` at whatever gdbstub
already exists, and giving it BOTH binaries' symbols. That's real value regardless of which
gdbstub (if any) the porter's loader happens to run.

## Why the `.so`'s runtime base address is left as an editable placeholder

GDB's `add-symbol-file <path> <base-address>` needs the actual address the loader mapped the
`.so` to -- a property of that loader's own relocation logic, decided fresh on every run
(ASLR, allocator behavior, or just a different build). No static analysis of the project's files
can know that number ahead of time; guessing a plausible-looking address and writing it into the
script as if confirmed would be actively worse than an obvious placeholder, since a wrong-but-
plausible address produces GDB output that LOOKS like resolved symbols while pointing at the
wrong code. The generated script's placeholder is deliberately impossible to mistake for a real
value, with a comment telling the porter exactly how to get the real one (log it once from the
loader itself).

## Why `watch_for_so_base()` reuses `debugnet_server.py`'s wire convention instead of inventing its own

A porter's loader almost always ALREADY has some way to print a debug line (their own
`debugnet`-style call, or nothing more than a raw UDP `printf`) -- requiring a NEW,
`gdb_bridge.py`-specific log format would mean writing (and testing) another one-off logging
call just for this. Accepting the exact same "one plain-text line per UDP datagram" convention
`debugnet_server.py` already established means a porter who already logs `[INFO] loader ready`
can add one more line (`SO_BASE=0x81000000`) to an existing call, on an existing port, instead of
wiring up a second listener. `watch_for_so_base()`'s regex is deliberately tolerant of `=` or `:`
and surrounding text for the same reason: it should work with whatever log-line shape the
porter's debug call already produces, not force a specific one.

## Why `elf_path`/`so_path` discovery is duplicated here instead of imported from `crash_analyzer.py`

Same convention as `so_patcher.py`/`mem_align_analyzer.py`: each module keeps its own small
private discovery helper rather than importing another module's underscore-prefixed function,
so this module's behavior doesn't silently change if `crash_analyzer.py`'s heuristic changes for
crash-analysis-specific reasons unrelated to debugging setup.
