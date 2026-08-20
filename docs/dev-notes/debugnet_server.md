# `debugnet_server.py` — Developer Notes

## Why the wire format is "one plain-text line per UDP datagram", not a specific binary protocol

"debugnet"-style remote logging on PS Vita homebrew isn't one single standardized library with a
fixed binary frame format -- it's a family of small, similar community libraries (and
project-specific variants) that all do roughly the same thing: format a string on-device and
`sendto()` it as one UDP datagram per log call. There's no ground truth for this in the current
port collection to verify against (no port here uses networked debug logging yet, unlike
`template.xml`/FalsoJNI's `java.c`, which real ports do ship and could be checked directly), so
this deliberately implements the simplest, most common convention -- decode each received
datagram as UTF-8 text, one line -- rather than guessing at a specific binary header/framing that
might not match whatever a given port's logging library actually sends.

If a specific project's variant DOES frame packets differently (a length-prefixed header, a
binary log-level byte, etc.), every received datagram's raw bytes still get written to the
session log file, so nothing is silently lost -- it just won't be nicely decoded/colorized until
this module's `run_live_log_server()` is adjusted for that specific format. This mirrors the same
posture as the LiveArea `template.xml` and JNI-stub scoping decisions elsewhere in this plan:
ship what's verifiable, be explicit about what isn't.

## Why the port defaults to 9999, not 1337

The plan item this responds to suggested both `1337` and `9999` as example defaults. `1337` is
already `config.DEFAULT_VITA_PORT` in this codebase -- VitaShell's FTP port. Defaulting the UDP
log listener to the same number, even though FTP (TCP) and this (UDP) occupy independent port
namespaces and wouldn't actually conflict, would misleadingly suggest they're the same "port
slot" in config/UI text and docs. `9999` avoids that confusion.

## Why severity coloring is a regex over the message text, not a structured field

Nothing about a plain "one line per datagram" protocol carries a structured severity field --
whatever severity marker appears (if any) is just whatever the calling code's own format string
put there (e.g. `"[ERROR] %s"`). `_detect_level()` is a bracketed-marker search
(`\[(FATAL|ERROR|WARN(?:ING)?|INFO|DEBUG)\]`) applied to the decoded text, matching exactly the
convention the plan item asked for -- and a line with no marker still displays and saves
normally, just without a color, rather than being dropped or misclassified.
