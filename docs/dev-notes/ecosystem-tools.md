# External ecosystem tools (2026 AI-porting guides)

Tools recommended by the r/VitaPiracy "Definitive guide" and "How to port games to Vita"
posts, checked against their real repos. Everything lives in `~/vita-tools/`;
`psvita-toolkit doctor` reports them in the "Optional AI/retail ecosystem" section.

| Tool | Repo | State on this Mac | Used by the toolkit |
|---|---|---|---|
| psp2_core_parse | shoui520/psp2_core_parse | `pip -e`, CLI `psp2-core-parse` | `analyze` writes `<dump>.psp2-core-parse.txt` |
| psp2_gpucrash_parse | shoui520/psp2_gpucrash_parse | `pip -e`, CLI `psp2-gpucrash-parse` | `analyze` on `*GPUCRASH*` dumps |
| psp2_err | shoui520/psp2_err | `pip -e`, CLI `psp2_err` | `errcode` fallback when the local DB has no match |
| vita-agent-use | shoui520/vita-agent-use | plugins built → `~/vita-tools/plugins/` | no (agent CLI, see below) |
| libperf | shoui520/libperf | built (`libperf.skprx`, `libperf.suprx`, stubs) | no |
| psp2-cex-gpues4-prof | shoui520/psp2-cex-gpues4-prof | built (static libs) | no |
| DMR | GrapheneCt/DMR | release v1.00 → `plugins/DMR.skprx` | no |
| CapUnlocker | GrapheneCt/CapUnlocker | release v1.4 → `plugins/CapUnlocker.skprx` | no |
| psp2-tooling | Grarak/psp2-tooling | cloned only | no |
| vkvita | DrDecki/vkvita | cloned only (GPL-3.0) | no |
| vitaGL | Rinnegatamante/vitaGL | cloned as source reference | no — the installed `libvitaGL.a` is a custom build, do not overwrite |

## Memory: DMR (365 → 381 MiB)

Install `DMR.skprx` under `*KERNEL`, 3.65 only. In the app:

```c
/* In libSceAppMgr_stub.a, but VitaSDK ships no prototype. */
int sceAppMgrGrowMemory(SceSize size, int killMiniApps);
/* early in main(), before the big allocations / newlib heap sizing */
sceAppMgrGrowMemory(16 * 1024 * 1024, 1);
```

## 4th CPU core: CapUnlocker

Install `CapUnlocker.skprx` under `*KERNEL`. It patches the affinity check so game apps can
pin threads to core 3: `0x00080000` (`SCE_KERNEL_CPU_MASK_USER_3`, not defined in VitaSDK).
It also allows `cache0:` mounts, the ForVM/ForMono threadmgr functions and every thread attribute.

## Clocks

333 MHz CPU / 166 MHz GPU by default, 444 MHz with `scePowerSetArmClockFrequency(444)`. 500/222
needs an overclock plugin, and that is the user's call (more heat). Always measure on the console.

## vita-agent-use (agent drives the console)

Lets an agent screenshot, send input, launch apps, upload files and get crash events pushed live.
- 3.65 only. **Incompatible with Vitacompanion**; BGFTP/catlog/kvdb/psp2shell/vdbtcp not recommended.
- Install by hand: copy `vita_agent_kernel.skprx`, `vita_agent_loader.suprx`, `vita_agent_shell.suprx`
  to `ur0:tai/`, add `*KERNEL ur0:tai/vita_agent_kernel.skprx` and `*main ur0:tai/vita_agent_loader.suprx`, reboot.
- PC: `python3 client/vita_agent.py bootstrap`, export `VITA_AGENT_CONFIG_DIR`, `./vita-agent session pair`
  (tap OK on the Vita), keep `./vita-agent serve` running for live crash/log events.
- Claude skills installed from its `docs/`: `vita-coredump`, `vita-gpucrash`, `vita-err`, `vita-perf`, `vita-gpuprof`.

## psp2-tooling (Grarak)

Upstream says Linux-only; on this Mac use `psp2-mac <tool> [args]` (`~/vita-tools/psp2-tooling-mac`):
`lsusb`/`timeout` shims + pyusb for the USB tools, Docker (`psp2-tooling-libdw`) for the libdw unwinders.
Example: `psp2-mac devkit/parse_core.py --elf app.elf --backtrace --gdb-core out.core dump.psp2dmp`.
Offline pieces that work on any dump/capture:
`devkit/parse_core.py` (writes an ARM core for gdb), `razor/rzlv.py`, `razor/sgx_capture.py`,
`profile/perf_data.py` (→ perf.data / hotspot), `pgo/` (clang PGO runtime for the Vita).

`retail-cp/` (turns a retail Vita into a DevKit over USB) is **not installed**:
- PCH-2000 on 3.65 only, and it needs decrypted DevKit 3.65 `os0:kd` modules that you have to obtain yourself.
- It sets dev-mode/hardware-debug switches in Syscon's boot block that persist. Only `devkit/retail_vita.sh uninstall`
  clears them, so never remove it from `config.txt` by hand.
- MTP is gone while it is active.

## vkvita

Vulkan 1.0-style wrapper over sceGxm, static lib. Only for sources that already target Vulkan or SDL3 GPU.
No compute, 960x544 swapchain only. Read `docs/COVERAGE.md` and `docs/SHADERS.md` first.
**GPL-3.0**: check the license before any closed release.
