# `cli.py` — Developer Notes

## Why this is a separate module instead of `argparse` calls inline in `__main__.py`

`__main__.py`'s `main()` is the interactive TUI loop and is used directly (e.g. by anything that
wants to force the TUI regardless of `sys.argv`). Splitting the headless dispatch into its own
`cli.py` keeps that loop untouched — `run()` (the new real entry point used by
`bin/psvita-toolkit` and `python3 -m psvita_toolkit`) just asks `cli.dispatch(argv)` "was this a
subcommand?" and only calls `main()` if the answer is no. No existing caller of `main()` had to
change.

## Why subcommands call private (`_`-prefixed) functions in other modules directly

`build_deploy._run_build()`, `build_deploy._find_output_vpk()` are the actual non-interactive
build logic; `build_and_deploy_wizard()` is just the TUI wrapper around them (target picker,
preset picker, then these calls). Rather than duplicate that logic or force a refactor to make
them public, the CLI reaches in directly — same package, same trust boundary. The alternative
(promoting them to public API) would imply a stability contract for functions whose signatures
still legitimately change as the TUI wizard evolves.

## Why headless mode never calls `ensure_global_config()`

That function prompts interactively for any missing required key — exactly the blocking
behavior a script/CI run can't tolerate. `_load_global_config()` instead just checks what's
already saved and fails fast with a clear message ("run the interactive TUI once to set this
up") if something required is missing. First-time setup is still a one-time interactive step;
every run after that is fully headless.

## Why `analyze()`/`_prompt_cmake_options()`/`choose_vpk()`/`upload_eboot()` grew new parameters instead of the CLI reimplementing them

`crash_analyzer.analyze()` was already fully non-interactive (it only prints/writes a report) —
the CLI calls it as-is, and it was changed to return `True`/`False` instead of always `None` so
the CLI's exit code reflects whether the dump was actually found and parsed. The build/deploy
path needed small additions instead: `_prompt_cmake_options(..., non_interactive=True)` keeps
every discovered `CMakeLists.txt` `option()` at its declared default rather than prompting (used
by the `build.sh`-less fallback), and `choose_vpk(..., non_interactive=True)` /
`upload_eboot(..., assume_yes=True)` skip the picker/confirmation the TUI needs but a script
can't answer. Every one of these defaults to the old interactive behavior when the new parameter
is omitted, so the TUI call sites didn't need to change.

## Why `init` reuses `init_port.py`'s own pipeline functions instead of a parallel implementation

`run_wizard()` was already split into independent steps (`setup_repo_dir`, `place_apk_and_detect`,
`decompile`, `git_init_and_ignore`, `write_plan_and_progress`, `write_claude_md_and_skills`,
`write_project_config`) with only `prompt_inputs()` and the top-level glue being interactive.
`run_wizard_headless()` builds the same `ctx` dict `prompt_inputs()` would have — from CLI
arguments instead of `input()` calls — and then runs the exact same pipeline, so any future fix
to how a port gets created applies to both the TUI and headless paths automatically.
