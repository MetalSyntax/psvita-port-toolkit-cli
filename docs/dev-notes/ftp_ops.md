# `ftp_ops.py` — Developer Notes

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
