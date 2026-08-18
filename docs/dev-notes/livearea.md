# `livearea.py` — Developer Notes

## Where this came from

This module is `convert_livearea.py` (a previously-standalone script) folded into the toolkit.
The image-processing logic (crop/fit/stretch resize, 8-bit indexed PNG conversion) is unchanged;
what changed is the output location — instead of a single path hardcoded to one game, the
destination is now derived from whichever project is currently active
(`<project_dir>/extras/livearea/`), so the same code serves every port without editing a
constant at the top of the file.
