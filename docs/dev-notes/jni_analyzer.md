# `jni_analyzer.py` — Developer Notes

## Why the "callback candidate" scan is source-based, not binary-string-based

The plan item this responds to (V2, item 10) describes scanning "símbolos `Java_*` del `.so` y
llamadas nativas en el Java de JADX" as if generating FalsoJNI stubs meant reverse-engineering
which method-name/signature string pairs the `.so` passes to `GetMethodID`/`GetStaticMethodID` at
runtime -- that requires disassembling every call site (the string literals for a method's name
and its signature aren't reliably adjacent in `.rodata`, so pairing them from raw strings alone is
genuinely ambiguous without per-call-site analysis).

Reading a real port's `lib/falso_jni/java.c` clarified what's actually needed: `NameToMethodID`/
`Methods*` there are the STUB SIDE of calls the `.so` makes *back into* "Java" (`CallVoidMethod`,
etc.) for methods that were never exported by the `.so` at all -- they're genuine Java methods,
which means jadx already has their exact name, return type, and parameter types with zero
ambiguity. The generalization used here: every jadx-decompiled class that declares at least one
`native` method (a "bridge" class, e.g. `Natives.java`) almost always also declares the plain
Java methods the engine calls back through, in that SAME class (confirmed against a real port,
ILLUSIA-vita's `com.gamevil.nexus2.Natives`: 14 `native` declarations plus 40 real callback
methods with full bodies, e.g. `OnSoundPlay`, `openUrl`, `getPhoneModel` -- exactly what a
`GetStaticMethodID`-based bridge class looks like). Scanning for "every non-native method in a
class that has a native method" is unambiguous, needs no binary reverse-engineering, and is
exactly what step 7 of the `so-crash-triage` skill already tells a developer to do by hand
("buscar la implementación Java real antes de adivinar") -- this automates finding *which*
methods those are, not the semantics of what they should do.

## Why it's still called a "candidate" list, not an auto-registration

Not every class with a native method is necessarily a JNI callback bridge (a class could
coincidentally declare an unrelated `native` method and unrelated ordinary methods), and not
every ordinary method in a bridge class is necessarily called via JNI (some might just be
internal Java helpers the native side never touches). `generate_jni_stubs()` doesn't try to
resolve that ambiguity -- it generates the FalsoJNI-shaped scaffolding (`NameToMethodID` entries,
per-return-type `Methods*` arrays, stub function bodies with correct `va_arg` extraction) for
every candidate, and leaves confirming "does the `.so` actually call this by name" and "what
should this stub actually do" to the porter, who has the real Java implementation right there in
the same jadx output to read. This mirrors the same posture as `generate_uniform_skeletons()` in
`utils.py` and the LiveArea `template.xml` scoping decision: generate a verified-structurally-correct
starting point, don't fabricate confidence about runtime behavior this tool can't observe.

## Why generated stub bodies use `va_arg` promotion rules, not project-specific guessing

Default argument promotion (integer types narrower than `int` promote to `int`, `float` promotes
to `double`) is guaranteed by the C standard for any argument matched against a function's `...`
parameter -- true for any C compiler, not a guess specific to this codebase. Since the game's
original `.so` was itself compiled as ordinary C/C++ and calls `JNIEnv::CallXMethod(...)` (a
standard variadic JNI function) directly, its call sites are subject to the same standard
promotion rules before `FalsoJNI`'s `va_list` plumbing ever sees the arguments. That's what
justifies generating `(jint) va_arg(args, int)` for an `int`/`boolean`/`byte`/`short`/`char`
parameter and `(jfloat) va_arg(args, double)` for a `float` one, rather than leaving every
argument extraction as a TODO for the porter to figure out from scratch.

## Why the top-level-member scanner tracks brace depth instead of using one big regex

A flat, whole-file regex for "public/private ... TYPE NAME(...) {" would also match methods of
anonymous inner classes (`new Runnable() { public void run() {...} }`, seen for real in
`Natives.java`'s `hideLoadingDialog()`/`hideTitleComponent()`) and interface method declarations
inside a nested `interface EventListener { ... }` -- both of which are irrelevant noise for this
purpose (a `Runnable.run()` override isn't a JNI callback target; an interface's abstract method
has no implementation to read). `_iter_top_level_members()` walks the class body tracking brace
depth so only members declared directly inside the top-level class -- not inside any nested
block -- are considered; verified against `Natives.java`, it correctly extracts exactly the 14
native + 40 callback methods and excludes every nested `run()`/interface declaration.

## Why the class-body scan is scoped to one top-level `class` per file

Same convention already established for `crash_analyzer.py`'s `_jni_method_name_from_symbol()`:
jadx overwhelmingly emits one public top-level class per file matching the filename, and that's
where a JNI bridge class's native methods live in every real port examined here. A native method
declared inside a nested class wouldn't be picked up -- an accepted limitation, not a silent
failure, since `scan_bridge_classes()` simply won't produce an entry for that file rather than
producing a wrong one.
