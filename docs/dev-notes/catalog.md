# `catalog.py` — Developer Notes

## Why this exists as its own module instead of folding into `doctor.py`/`project.py`

`doctor.py` answers "is my environment set up correctly"; `catalog.py` answers a different
question entirely -- "what does this toolkit even do" -- for a toolkit that grew to 27 tools
across a dozen modules over several rounds of work, past the point the user (or a future
contributor) can hold the whole feature set in their head. Keeping it separate means neither
module's reason for existing gets diluted, and the catalog can be read from three places
(the project-selector root menu, the per-project Utilities submenu, and `psvita-toolkit tools`
headless) without any of them needing to know about the others.

## Why the same `CATALOG` structure backs both the TUI and the web-dashboard version

A published web reference and a terminal one for the same 27 tools, kept in sync by hand across
two separate documents, drift the moment either one gets a new tool added and the other doesn't.
Grouping `CATALOG` exactly the way the web artifact groups things (by where a tool enters the
porting workflow -- diagnostics, build, LiveArea/shaders, triage, reverse engineering, real
console, ecosystem -- not by which Python file implements it) means adding a 28th tool later is
one entry in one place, read by whichever surface the person happens to be using.

## Why descriptions are `{"es", "en", "pt"}` dicts read directly, not `i18n.register()`+`t()` keys

Every other user-facing string in this toolkit is a short menu label or prompt with one obvious
lookup key. A tool's description is a full sentence with no natural "key" of its own other than
the tool's name -- registering 27 (or 54, counting group descriptions) separate `catalog.*` keys
in the global `i18n` string table would be pure bookkeeping overhead for content that's only ever
read in this one place. Storing the three languages directly on each `CATALOG` entry and picking
`i18n.get_language()`'s language at print time keeps full trilingual support without that
overhead -- `print_catalog()` is the only code that ever reads these dicts.

## Why `print_catalog()` never calls `tui.clear()`/`tui.pause()` itself

`tui.run_menu()`'s own loop already clears the screen and prints a "→ <label>" header right
before invoking any selected item's callback, and already calls `pause()` right after it returns
-- unconditionally, regardless of what the callback did. An early version of this module wrapped
`print_catalog()` in a `catalog_menu()` that also cleared and paused, which would have meant
pressing Enter twice from the project-selector menu (which happens to route through
`doctor.doctor_menu()`, a case that already does exactly this for Doctor). Calling
`print_catalog` directly as the menu item's callback -- the same way `doctor.run_doctor` is
already used from the per-project Utilities submenu -- gets exactly one clean pause, matching the
less surprising of the two patterns already living in this codebase.
