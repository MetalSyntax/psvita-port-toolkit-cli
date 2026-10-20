# Plan General — Características Extras para los Ports (gap vs. escena)

> Objetivo: llevar los ports propios (Zenonia 2/3/4, Dungeon Hunter 2/3, Asphalt 5/6,
> Advena, ILLUSIA 1/2, Inotia 3, resto Gameloft/Gamevil) al nivel de extras de los
> ports de referencia de internet (TheFlow, Rinnegatamante, gl33ntwine/v-atamanenko,
> frobnicator, Nevak, etc.), con un diseño **común y reutilizable** en vez de
> parches ad-hoc por juego.
>
> Verificación local (2026-09-22): ninguno de los 6 repos muestreados
> (`Zenonia2/3/4-vita`, `Dungeon-Hunter-2-vita`, `Asphalt-5-Vita`, `Advena-Vita`)
> tiene `trophy/`, configurator app, `settings.bin`, `manual/`, ni `controls.txt`.
> Ese es el gap real, no una suposición.

## Fuentes analizadas

| Port / repo | Extras que aporta (y los nuestros no tienen) |
|---|---|
| `TheOfficialFloW/gtasa_vita` (GTA SA) | Configurator app desde LiveArea, `controls.txt` remapeable, L2/R2→rear touch y L3/R3→front touch (conmutable), teclado OSK con L+SELECT para cheats, quick-save al salir + Resume=carga último save, canciones de radio recortadas restaurables (`MUSIC.md`), MP3 fuzzy seek, texturas HD opcionales, render PS2-like conmutable |
| `Rinnegatamante/raider-vita`, `ff4_vita`, `fahrenheit-vita`, `not_a_hero-vita` | **Trofeos** (incl. ocultos + iconos hi-res), multilenguaje (hasta 7 idiomas, TR2), FMV por transcode ffmpeg `.bat` opcional, `datafiles.zip` separado, front-touch mapping L2/L3/R2/R3, nota PSVshell 500 MHz |
| `v-atamanenko/soloader-boilerplate` (Solobop) | `settings.bin` persistente (`source/utils/settings.c`), redirect `-config` a binario configurador, overclock 444/222/222/166 al arrancar, `SHADER_FORMAT` GLSL/CG/GXP + `DUMP_COMPILED_SHADERS`, targets CMake `send/dump/reboot` vía `PSVITAIP` |
| `v-atamanenko/masseffect-vita`, Backstab HD, Dead Space (gl33ntwine) | Companion/configurator lanzable con botón **Settings en el LiveArea**, deadzones de sticks, nivel de detalle gráfico, FPS limiter, sensibilidad, `Control Scheme 2` documentado |
| `Nevak/bgda-vita` (Dark Alliance) | L2/R2 en rear touch, idioma = idioma del sistema Vita, coop 2 mandos PSTV, Battery Saving = lock 30 FPS, logros marcados como "not yet" (roadmap honesto) |
| Escena general (VitaDB Downloader, reRescaler, VitaGrafix, PSVshell, reVita) | Overclock/Manual FPS counter como expectativa del jugador, VitaGrafix-style res/FPS, manual LiveArea, bg music en LiveArea/Downloader, auto-updater + changelogs visibles |

## Gap analysis resumido

| # | Extra de la escena | Estado en nuestros ports | Prioridad |
|---|---|---|---|
| 1 | Remapeo de controles (`controls.txt` + esquema Vita mejorado) | ❌ Hardcodeado en `source/input.c` | P0 |
| 2 | L2/R2/R3 trasero/delantero + soporte PSTV real | ❌ Parcial / sin documentar | P0 |
| 3 | Deadzones + sensibilidad configurables | ❌ | P0 |
| 4 | Configurator app + botón Settings en LiveArea + `settings.bin` | ❌ (boilerplate lo soporta, ningún port lo usa) | P0 |
| 5 | Overclock automático 444 MHz + mención PSVshell | ⚠️ Parcial (algunos lo fijan, sin opción ni docs) | P0 |
| 6 | Trofeos (`sce_sys/trophy/`, ocultos, iconos hi-res) | ❌ | P1 |
| 7 | Idioma = idioma del sistema + fallback | ❌ (rutas `eng/` hardcodeadas) | P1 |
| 8 | LiveArea premium (manual, frames, bgm, botón config) | ⚠️ Solo bg/pic/icon básicos | P1 |
| 9 | Quick-save/auto-resume + import saves Android | ❌ | P1 |
| 10 | Cheat/debug OSK (L+SELECT) donde aplique | ❌ | P1 |
| 11 | Opciones gráficas (res interna, MSAA/bilinear, FPS limiter, PostFX/CRT) | ⚠️ Solo flags de build sueltos | P1 |
| 12 | Shader cache en disco + primer arranque rápido | ⚠️ Inconsistente | P1 |
| 13 | Videos/FMV con script transcode + skip elegante si falta | ⚠️ Solo Asphalt 5 lo hace bien | P2 |
| 14 | `datafiles.zip` + checker de archivos + mensajes de error con diálogo | ⚠️ Desigual | P2 |
| 15 | Parches version-agnostic + multi-versión APK soportada | ❌ (un APK exacto por port) | P2 |
| 16 | Release estándar VitaDB (screenshots, trailer, changelog, QR, gamefiles.zip) | ⚠️ Desigual | P2 |

## Fase P0 — Quick wins (1–2 tardes por port, alto valor jugador)

### P0.1 Controles remapeables + `controls.txt`
- Copiar el patrón `gtasa_vita`: `ux0:data/<juego>/controls.txt` con parsing
  simple `VITA_BOTON = ACCION_JUEGO`, + esquema por defecto "Vita mejorado".
- Mantener compat: si no existe el archivo, usar el mapeo actual (cero regresión).
- Documentar en README con tabla (como ya hace Asphalt 5, que es el mejor ejemplo propio).

### P0.2 L2/R2/R3 touch + PSTV
- Estándar escena: **L2/R2 = rear touch arriba, L3/R3 = front touch abajo**,
  con alternativa L2/R2 = front arriba (la que añadió GTA SA v1.2 para PSTV).
- Donde el juego no necesite L2/R2, mapearlos a acciones útiles
  (poción/mapa/save rápido) en vez de dejarlos muertos.
- Probar explícitamente en PSTV (o declarar "PSTV requiere DS3/DS4 por MiniVitaTV").

### P0.3 Deadzone + sensibilidad
- Exponer 2 valores en settings (default: inner ~8–12): evita drift y quejas de
  "stick no llega al máximo en diagonal" (cf. AnalogsEnhancerKai).
- Backstab HD lo pide hasta en su guía ("bajar sensibilidad al mínimo") — mejor
  poner defaults sanos desde el loader que pedirlo al jugador.

### P0.4 Configurator mínimo + `settings.bin`
- Activar lo que Solobop ya da gratis: `settings.c` + redirect `-config` +
  botón **Settings en LiveArea** (ejemplo: Mass Effect, Backstab HD).
- Contenido mínimo v1: deadzones, detalle gráfico (si el port tiene flags),
  FPS limiter on/off, toggle overlay táctil (Zenonia 4 ya oculta gamepad virtual;
  hacerlo setting en vez de `#define`).
- El toolkit (`psvita-toolkit`) puede generar el esqueleto del configurador al
  crear/adoptar un port — no copiarlo a mano en cada repo.

### P0.5 Relojes + PSVshell
- Fijar `444/222/222/166` al arrancar (ya lo hace `soloader_init_all()`; verificar
  que todos los ports lo llamen) + línea estándar en README:
  "Opcional: PSVshell a 500 MHz si algún nivel cae".

## Fase P1 — Nivel escena (1 semana por port aprox.)

### P1.1 Trofeos
- Seguir `raider-vita`/`ff4_vita`: `sce_sys/trophy/` con `TRP` + iconos hi-res,
  un par ocultos para mimetizar el pack original donde aplique.
- Definir lista corta y farmeable (boot, primer boss, fin capítulo, 100%):
  nada de trofeos imposibles por bugs conocidos.
- `TropHAXSE`-friendly: que desbloquear sea llamada real, no flag local.

### P1.2 Idioma del sistema
- Leer idioma de Vita (`sceAppUtilSystemParamGetById`) y mapear a carpeta de
  assets (`eng/spa/fra/...`); fallback a inglés. Como `bgda-vita`.
- Aplica directo a Zenonia/DH/Inotia (ya traen varias carpetas de idioma en el APK).

### P1.3 LiveArea completo
- `template.xml` con frames + botón Settings/Config + **manual** (`sce_sys/manual/`,
  convertir FAQ actual con FAQ-to-Manual) + bgm `at9` opcional.
- El módulo `livearea.py` del toolkit ya valida specs; añadirle plantillas
  "premium" (manual + botón config) en vez de hacerlo a mano por port.

### P1.4 Saves: quick-save + import Android
- Quick-save al elegir Quit (patrón GTA SA) + Resume = último save.
- Script/doc "copia tu save de Android a `ux0:data/<juego>/`" (ruta exacta por juego).

### P1.5 Opciones gráficas en runtime (no solo flags de build)
- Mover NEON/turbo/downsample/frameskip de `build.sh --flags` a settings:
  `Detalle: Alto/Bajo`, `FPS lock: 30/60/off`, `Filtro: bilinear/sharp`,
  `Velocidad: 1x–3x` (el ciclo `R+SELECT` de Zenonia 4 es buen patrón, pero que
  sea setting persistente, no solo toggle volátil).
- CRT/PostFX solo si vitaGL lo da gratis; no inventar shaders propios por port.

### P1.6 Shader cache
- `DUMP_COMPILED_SHADERS=ON` + avisar "primer arranque tarda, los siguientes no"
  (Solobop ya lo implementa; activarlo y documentarlo en todos).

## Fase P2 — Pulido release (cuando P0+P1 estén)

- **FMV**: patrón `raider-vita`/Asphalt 5: `.bat`+ffmpeg opcional, skip elegante si
  falta el `.mp4` (nunca colgar).
- **Checker de datos**: al arrancar sin `.so`/assets, diálogo `SceCommonDialog`
  con la ruta exacta que falta (no crash mudo `C2-12828-1`).
- **`datafiles.zip`** separado del VPK para lo convertible (texturas/shaders CG).
- **Parches version-agnostic** (patrón `ff4_vita`): buscar por firma, no por offset
  duro, y listar APKs probadas en el README.
- **Release checklist VitaDB**: VPK + screenshots + trailer 30 s + changelog +
  `gamefiles.txt` + QR Brewology + nota kubridge/FdFix/libshacccg.

## Orden de aplicación sugerido (tus repos)

1. **Asphalt-5-Vita** (el más maduro: ya tiene controles documentados, audio fixed-point,
   video-skip) → piloto de P0.1+P0.4+P1.5.
2. **Zenonia4-vita** (turbo/frameskip ya existe) → convertir flags a settings P0.4+P1.5.
3. **Dungeon-Hunter-2-vita** → P0.2+P0.5 (ayuda al FPS bajo sin tocar el motor).
4. **Zenonia2/3, ILLUSIA, Inotia3, Advena** → aplicar el paquete P0 ya rodado en masa.
5. Trofeos (P1.1) en oleada común una vez el configurador exista (mismo `settings.bin`
   puede guardar progreso de trofeos si hace falta).

## Automatización con el toolkit (qué hace la máquina, qué pide IA)

> Estado real auditado 2026-09-22 con `psvita-toolkit log-standard`:
> `asphalt5` conforme (0 discrepancias, es el estándar canónico);
> `advena` 3 (sin `next.idx`, path hardcodeado, sin flag de producción);
> `dungeon-hunter-2` 4 (basename `log_NNN` sin slug, base 000, sin `next.idx`, path hardcodeado);
> `zenonia2` sin logger (esquema viejo `log_<timestamp>.txt`).

| Extra del plan | Toolkit lo automatiza | Requiere IA / mano |
|---|---|---|
| Subir `logs/`+`saves/` vacíos por FTP | ✅ Sí — `upload_ux0_data_dir` ahora crea dirs vacíos (fix); `scaffold_ux0_data` deja `.gitkeep` (el uploader lo salta como payload pero crea el dir remoto) | — |
| Estándar `<slug>_001..999` + `next.idx` + `DATA_PATH` + flag prod | ✅ `log-standard [--fix-dirs]` audita y crea staging; el `PORTING_PLAN.md` nuevo ya trae la sección 6 con la norma | Migrar cada logger a mano (plantilla: el de Asphalt 5), la herramienta solo señala el gap |
| Trazas `[TRACE] >> f()` detalladas sin IA | ✅ `log-trace [--dry-run] [--ensure-flag] [--remove]` — regex determinista, entry-only (las de salida con returns/gotos mentirían), idempotente, bloque `PORT_TRACE` (ON=debug, OFF=producción, verificado que compila con `gcc -fsyntax-only`) | La IA las *lee*, no las escribe: con trazas homogéneas el `export-context` le da a cualquier copiloto el mismo formato en todos los ports |
| Controles remapeables, configurador, trofeos, LiveArea premium | ⚠️ Parcial — `sync-shared` propaga un componente ya hecho (ej. `controls.txt`, `settings.c`) a toda la familia de motor; `livearea --validate` y `jni-analyze` cubren su parte | Diseñar el componente una vez (en el piloto Asphalt 5) sí es trabajo de port |
| Release VitaDB | ⚠️ `build`+`deploy`+`analyze` ya headless para CI | Screenshots/trailer/changelog a mano |

Flujo recomendado por port: `log-standard --fix-dirs` → migrar logger → `log-trace --ensure-flag`
para cazar el bug → `log-trace --remove` (o build con `PORT_TRACE=OFF`) antes del VPK final →
`export-context` para que la IA diagnostique sobre logs homogéneos.

## Criterio de "hecho" por port

- [ ] `controls.txt` funciona y está documentado
- [ ] L2/R2/R3 tienen función en Vita y PSTV
- [ ] Botón Settings en LiveArea abre configurador (deadzones, detalle, FPS lock)
- [ ] Relojes fijados + nota PSVshell en README
- [ ] Trofeos desbloquean (testeado con TrophyShot/TropHAXSE visible)
- [ ] Idioma sigue al sistema con fallback
- [ ] LiveArea con manual + frames, sin warning `0x8010113D`
- [ ] Release con changelog, screenshots, `gamefiles.txt`, versiones APK probadas

## Notas anti-fabricación

- No prometer online/multijugador resucitado, 60 FPS fijos donde el motor no da,
  ni contador GPU (PowerVR no lo expone a homebrew) — la telemetría honesta es
  frame time, como ya hace `perf_telemetry.py`.
- Todo lo de arriba existe y está referenciado en ports públicos; nada requiere
  investigación nueva, solo portar el patrón al esqueleto común y rodarlo por
  todos los juegos.
