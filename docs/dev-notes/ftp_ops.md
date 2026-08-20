# `ftp_ops.py` — Developer Notes

## Why `verify_data_assets()` now opens one FTP connection instead of one per subfolder

The original version called `_connect()`/`_quit()` inside the per-subfolder loop -- for a game
with a dozen data subfolders, that's a dozen full connect/login/quit cycles for what's
conceptually one comparison operation. VitaShell's ftpd doesn't handle rapid repeated
connect/disconnect gracefully (occasionally refuses the next `connect()` right after the
previous session tears down), so this was also the most likely place in the whole toolkit to
actually hit that problem. It now opens a single connection up front (via
`_connect_with_retry()`), sends `NOOP` between subfolders as a keep-alive, and only reconnects
if that `NOOP` reports the connection is actually dead -- one handshake for the whole operation
instead of one per subfolder, with the same resilience to a dropped connection mid-loop.

## Why `_connect_with_retry()` exists but ordinary `upload_vpk()`/`upload_eboot()` calls also use it

A single failed connection attempt right after VitaShell finishes tearing down a previous FTP
session is a known, transient failure mode, not usually a real "the console is unreachable"
situation. Silently retrying once or twice with a short delay before giving up and printing an
error saves the user from having to notice the failure and just press the same menu option
again. `_connect()` itself is untouched (still a single attempt) -- callers that want the retry
opt into it explicitly by calling `_connect_with_retry()` instead.

## Why the progress bar is a closure-returning factory, not a class

`_progress_callback(total_size, label)` returns a plain function closing over a small `state`
dict instead of a `Progress` class with methods -- `ftplib.FTP.storbinary()`/`retrbinary()` just
want a single-argument callable per data block, so a factory function matches that shape exactly
with no extra ceremony. It's throttled to ~1 redraw per 150ms specifically because on a fast LAN
transfer, printing on every 8KB block would spend more wall-clock time writing to the terminal
than the transfer itself takes.

## Why console profiles just overwrite `vita_ip`/`vita_port` instead of every call site reading an "active profile" indirection

Every FTP call site (`upload_vpk`, `upload_eboot`, `download_logs_and_dumps`, ...) already reads
`project_cfg["vita_ip"]`/`project_cfg["vita_port"]` directly. Threading a `console_profiles`
lookup through all of them would touch a lot of code for no behavioral difference.
`switch_console_profile()` instead just copies the chosen profile's `ip`/`port` into those same
two keys and persists it -- from every other function's point of view, nothing changed, "the
active console" is still just whatever `vita_ip`/`vita_port` currently say. Profiles
(`project_cfg["consoles"]`) are purely a named-shortcut layer on top of that.

## Where this came from

This module generalizes `manage_vita.py`'s FTP functionality (Advena's version, the most
evolved of the five original ports) — talking to the VitaShell ftpd to upload a VPK/eboot,
download logs/crash dumps, sync shaders, and verify data assets. The original scripts hardcoded
constants (IP, paths, TITLEID) at the top of the file per-game; here everything comes from the
active project's config (`project_cfg`/`project_dir`) instead, so one implementation serves
every port.

## VPN bypass rationale

`disconnect_vpn()` and `_local_ip_for_route()` exist because a VPN that routes all traffic
(e.g. a WireGuard/ProtonVPN tunnel used for other purposes, like reaching an SMTP port blocked
by a hosting provider) can break the LAN connection to the Vita entirely if FTP traffic gets
routed through the tunnel instead of the local network. `disconnect_vpn()` runs a best-effort,
user-configured disconnect command first; `_local_ip_for_route()` then forces the FTP socket to
bind to the physical LAN interface's IP (matching the Vita's `/24` subnet) as its source
address, so even a VPN that's still up doesn't hijack this specific connection.

## Why `verify_data_assets()` does a shallow comparison

A full recursive local-vs-remote diff sounds more thorough, but VitaShell's ftpd only supports a
limited number of concurrent data connections. A folder like `3d/` with thousands of
subdirectories would exhaust those connections if walked recursively over FTP, likely locking
up the transfer for anything else. The shallow (first-level-only) comparison trades
completeness for something that actually finishes and still catches the common failure mode
this was built for: assets that only got partially copied to the Vita.

## Why crash-dump/log download offers three modes (latest / pick-from-console / local history)

This was an explicit requirement, not just a nice-to-have: being able to grab the very latest
dump quickly during active debugging, but also to go back and pick a *specific* older dump
(either still sitting on the console, or one already downloaded before — the local "history")
without it being silently overwritten by the next capture. The original `get_dump.sh` script
this replaces only ever fetched the single latest file, always to the same local filename,
silently overwriting whatever was there before — exactly the gap this three-mode menu closes.
