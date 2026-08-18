# `init_port.py` — Developer Notes

## Why this doesn't copy `porting_tools/` into the new port

The original `init_new_port.sh` script copied a `porting_tools/` folder (build/deploy/FTP
scripts) into every new port's own repo, adapted with that port's specific paths baked in. That
was exactly the pattern this whole standalone toolkit exists to eliminate: five different copies
of nearly-identical scripts, each subtly out of date with the others. This wizard writes only a
single `.psvita-toolkit.json` into the new port instead — everything else (build, deploy, FTP,
LiveArea, crash analysis) is provided by this external toolkit, operating on whatever project
directory the user points it at.

## `_same_file()` — the macOS case-insensitive-filesystem bug

Discovered in real use: creating a port named `ILLUSIA-vita` when a folder `Illusia-vita`
already existed on disk crashed with `shutil.SameFileError`, even though the code had an
explicit same-file guard before the copy. The guard compared `Path.resolve()` output as
strings — but macOS's default filesystem (APFS) is case-insensitive, so `ILLUSIA-vita` and
`Illusia-vita` are literally the same directory (same inode) even though they're different
strings. `Path.resolve()` doesn't fold case, so the guard's string comparison said "different
paths," while `shutil.copy2()`'s own internal check (which does compare by inode) correctly
detected they were identical and raised. `_same_file()` now uses `os.path.samefile()` directly,
matching what `shutil` itself uses, so it agrees with `shutil` instead of contradicting it.

## `_merge_tree_no_clobber()` — the `cp -Rn` exit-status bug

The original merge step shelled out to `cp -Rn tmp_clone/. new_dir/` to copy the boilerplate
scaffold into the new port's folder without overwriting anything already there (e.g. an `.apk`
the user had already placed by hand). This is exactly the "resume after a previous attempt
failed partway through" scenario the wizard is supposed to support gracefully — but macOS's BSD
`cp` returns exit status 1 as completely normal behavior whenever `-n` causes it to skip even a
single already-existing file, regardless of whether the overall merge succeeded. Since the
surrounding code treated any non-zero exit code as a hard failure, re-running the wizard against
a folder that already had so much as one file in it (from a prior attempt) always crashed with
`CalledProcessError`, even though the merge itself would have worked fine. `_merge_tree_no_clobber()`
reimplements the same walk in pure Python, where "skip this file, it already exists" is just a
skip, not an error.

## Why `_own_titleid()` exists (resuming a failed init attempt)

If a previous attempt at creating a port already got far enough to write `CMakeLists.txt` (with
a real TITLEID) before crashing on a later step, re-running the wizard against that same folder
used to reject that exact TITLEID as "already in use" — because the collision check scans every
`CMakeLists.txt` under `BASE_DIR`, including the very folder being re-initialized. `_own_titleid()`
detects this case up front, excludes the project's own TITLEID from the collision set, and offers
it back as the default in `prompt_inputs()` -- pressing Enter resumes the same project instead of
being forced to invent a new TITLEID for what is, in fact, the same port.
