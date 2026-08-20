# `shader_live_reload.py` — Developer Notes

## Why this is "auto-upload on save", not the plan's literal "hot-patching in-game"

Reloading a shader inside a RUNNING game without restarting it needs that game's own code to
notice the file changed and re-run its shader-compile/bind step -- this toolkit has no way to
inject that behavior into a binary it doesn't own (same "can't own the loader" boundary
documented in `so_patcher.py`/`gdb_bridge.py`). What's entirely within this toolkit's control:
the edit -> validate -> upload part of the loop, which today means alt-tabbing to a menu, picking
the right upload action, and waiting. Automating exactly that part removes real manual friction
without pretending to solve the part that depends on code this project doesn't ship.

## Why one FTP connection is kept alive for the whole watch session

`ftp_ops.py`'s own docs already note VitaShell's ftpd occasionally refuses a new connection
attempt made right after a previous one just closed. A live-reload watcher that reconnects on
every single file save would hit that far more often than any other flow in this toolkit --
`_keepalive()` (a plain `NOOP`) is checked before each upload instead, and a fresh connection is
only opened if that actually fails.

## Why the first poll uploads every existing `.cg`, not just future changes

There's no persisted "last synced" state across runs, and starting with an empty `mtimes` dict
means the first poll always finds every file "changed". Treating that as a bug and skipping the
initial batch would leave the console out of sync with whatever was on disk before the watch
started; treating it as a feature (an implicit "sync now" on start) is more useful and simpler
than adding a separate first-run code path.

## Why an invalid shader is skipped, never uploaded

Reusing `utils.validate_shader()` (the same `psp2cgc` check every other shader path in this
toolkit already goes through) means a syntax error introduced mid-edit doesn't get pushed to the
console as a partially-broken `.cg` -- it's reported locally, in the same terminal the porter is
already watching, and the next successful save uploads normally.
