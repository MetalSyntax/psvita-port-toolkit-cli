# `livearea.py` — Developer Notes

## Why `generate_template_xml()` only offers the "a1 gate" layout

The plan this responds to (V2, item 5) asked for "selección de layouts clásicos de LiveArea
(botón central simple, layout con web link, layout multipágina)". Before writing a generator,
every real `template.xml` already shipped in this collection was read directly (Zenonia 2/3/4,
ILLUSIA 1/2, Inotia 3, Advena, and `soloader-boilerplate`'s own scaffold) -- every single one is
byte-for-byte the same minimal `style="a1"` "gate" layout (a background image + a startup banner,
tap anywhere to launch, no interactive tiles). There is zero real precedent in this codebase for
a "web link" or "multi-page" LiveArea layout.

Sony's richer LiveArea styles (scrolling banners, live-updating tiles) exist, but they require
the *game itself* to push content updates at runtime via `sceAppMgrLiveAreaAddContent` -- they
aren't just a different static XML template, they're a deeper runtime integration this toolkit
has no way to verify without a port that actually implements it. Rather than generate unverified
XML that could silently fail to render (or worse, fail to launch the game at all) on real
hardware with no easy way to test it in this environment, `generate_template_xml()` writes
exactly the one layout that's confirmed working across every real port here. If a future port
needs a fancier layout, verify it on hardware first, then extend `TEMPLATE_XML_CONTENT` (or add a
second confirmed template) rather than guessing at the schema.

## Why `bgm.at9` conversion can fail with "no encoder found", not silently produce a bad file

ATRAC9 is a proprietary Sony audio codec. Neither VITASDK nor ffmpeg ships an encoder for it
(confirmed: `ffmpeg -encoders` lists no `atrac9`/`at9` entry) -- a real encoder (`atrac9tool`)
only comes from Sony's official PS4/Vita SDK, which isn't something this toolkit can assume is
installed. `convert_bgm_to_at9()` deliberately refuses to produce `bgm.at9` when no encoder is
found, rather than e.g. silently renaming a `.wav` to `.at9` (which would sit on the Vita as a
file LiveArea can't actually decode) -- a clear "here's why, here's what to get" message beats a
byte-identical-looking-but-broken output file. If the input is already `.at9`, it's just copied
through as-is (no encoder needed).

## Where this came from

This module is `convert_livearea.py` (a previously-standalone script) folded into the toolkit.
The image-processing logic (crop/fit/stretch resize, 8-bit indexed PNG conversion) is unchanged;
what changed is the output location — instead of a single path hardcoded to one game, the
destination is now derived from whichever project is currently active
(`<project_dir>/extras/livearea/`), so the same code serves every port without editing a
constant at the top of the file.
