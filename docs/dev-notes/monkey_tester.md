# `monkey_tester.py` — Developer Notes

## Why `run_soak_test()` never auto-stops on a detected hang

An unattended multi-hour soak test is exactly the scenario where the porter isn't watching the
terminal live -- they might come back, notice the console froze, power-cycle it, and let the run
continue. Exiting on the first hang would throw away every heartbeat that arrives afterward and
under-report the actual total stable runtime, which is the opposite of what a certification
report ("Tested: N hours crash-free") is supposed to mean. Logging the incident and continuing to
listen keeps the final summary honest about BOTH the incident count and the real elapsed time.

## Why the "Tested: 0 incidents" badge is only ever written when it's actually true

`incidents == 0` is checked before anything gets appended to `PORTING_PLAN.md` -- a run with even
one hang never produces the badge line, on purpose. The plan's request for a quality badge only
has value if seeing it printed means something specific and true; writing a softened or
approximate version of it for a run that actually had incidents would make the badge meaningless
for every future reader of that file.

## Why `monkey_test_poll_input()` is generated as an explicitly-optional example, not wired in by default

Feeding a game synthetic random input has actual failure modes that have nothing to do with the
port itself -- a random button held at the wrong moment in a destructive menu (delete save,
confirm purchase-equivalent, exit without saving) could produce misleading "crashes" that are
really just the fuzzer doing something no real player would do. This toolkit can't know which
inputs are safe to fuzz for any specific game, so the generated function is explicit example code
behind the porter's own build flag, never auto-enabled -- the porter decides whether/how to guard
against destructive menu paths before turning it on.

## Why leak detection isn't duplicated here

`mem_profiler.py` already does exactly this (checkpoint-relative leak candidates over a live UDP
allocation stream) -- reimplementing it inside `monkey_tester.py` would mean two independent, and
likely eventually diverging, definitions of "what counts as a leak" for the same kind of run.
Running both tools side by side during one soak-test session is the documented way to get both
signals without that duplication.
