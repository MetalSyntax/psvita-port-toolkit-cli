# `automation_mac.py` — Developer Notes

## Where this came from

Folded in from `porting_tools/automation/*.py` (`click_helper.py`, `hold_click.py`,
`mousedown_only.py`/`mouseup_only.py`, `key_helper.py`) -- previously five separate standalone
scripts invoked as subprocesses, unified here into one importable module with the same
underlying Quartz calls.

## Why not just use AppleScript/accessibility clicks

The natural first approach for automating a click on Vita3K would be `osascript` targeting UI
elements via macOS accessibility APIs. That doesn't work here: Vita3K's UI is built with Qt,
which doesn't expose its widgets through the accessibility tree the way native Cocoa apps do.
AppleScript-driven "clicks" on such a window are silently ignored. Posting real `CGEvent`
mouse/keyboard events through Quartz (the same layer actual hardware input goes through)
works regardless of the target app's UI toolkit, which is why this module exists at all instead
of a simpler AppleScript one-liner.
