# psvita-port-toolkit

Herramienta standalone (TUI de flechas, sin dependencias exóticas) para llevar un port de
Android (soloader) a PS Vita **de punta a punta**: crear el port desde cero, compilar,
desplegar en una consola real por FTP, bajar logs y crash dumps, analizarlos,
sincronizar shaders, generar los assets de LiveArea, y más -- todo desde un solo lugar,
operando sobre **cualquier** carpeta de port que le indiques.

> **Nota:** no hay soporte de despliegue/build para el emulador Vita3K -- se eliminó tras
> confirmar (con Prince of Persia Classic) que sus limitaciones lo hacen inviable para este tipo
> de port. Ver `docs/dev-notes/build_deploy.md`.

Nace de consolidar `porting_tools/` de 5 ports reales (Zenonia 2/3/4, Dungeon Hunter 2, Advena) +
`init_new_port.sh` + `convert_livearea.py`, tomando el superset de funcionalidad de todos y
generalizando lo que estaba hardcodeado por juego. Reemplaza tener una copia de `porting_tools/`
en cada repo de port: ahora es una sola herramienta, en un solo repo, que ya sabe operar sobre
todos.

## Instalación

```bash
git clone <este-repo> psvita-port-toolkit-cli
cd psvita-port-toolkit-cli
pip install -r requirements.txt

# opcional, solo si vas a usar esa función puntual:
pip install deep-translator   # traducir los .md del proyecto en lote
```

Correr:

```bash
python3 -m psvita_toolkit
# o, si agregaste bin/ al PATH:
psvita-toolkit
```

## Primer uso

La primera vez pregunta (y guarda en `~/.psvita-toolkit/config.json`, no se vuelve a preguntar):

- **Idioma / Language / Idioma** -- Español, English o Português. Se guarda y queda fijo para
  todas las siguientes veces; cambialo cuando quieras desde "Configuración global" en el menú de
  selección de proyecto.
- **BASE_DIR** -- la carpeta donde viven todos tus ports (ahí es donde busca/lista los proyectos
  existentes).
- **soloader-boilerplate** -- el scaffold que se clona al crear un port nuevo.
- **Skills de Claude Code** -- se copian a `.claude/skills/` de cada port nuevo
  (`psvita-porting`, `so-crash-triage`, `psvita-port-init`).
- **VITASDK** -- para el analizador de crash dumps y la búsqueda de símbolos.

Después de eso, cada vez que abrís el toolkit elegís:

1. **Continuar con el último port** (un atajo directo si ya veniste trabajando en uno).
2. **Continuar con otro port existente** -- lista los que detecta bajo BASE_DIR (por
   `CMakeLists.txt` con `VITA_TITLEID`, o por tener ya `porting_tools/`), o ingresás una ruta a
   mano. Un port viejo (creado antes de este toolkit, sin `.psvita-toolkit.json` todavía) se
   **adopta**: se auto-detectan TITLEID/nombre/IP de lo que ya tiene y se confirma contigo antes
   de guardar la config.
3. **Crear un port nuevo desde cero** -- pide el `.apk`, detecta ABI/GLES, decompila con
   jadx + Ghidra (`devrvk/so-decompiler` vía Docker), hace `git init` con `.gitignore` anti-DMCA, y
   deja `PORTING_PLAN.md`/`port_progress.md`/`CLAUDE.md` escritos.
4. **Configuración global** -- para corregir cualquiera de esas rutas después.

## Menú principal (por proyecto)

Una vez dentro de un proyecto, todo es un menú de flechas navegable:

- 🔨 **Compilar y Desplegar** -- asistente guiado: destino (PS Vita física / solo
  compilar) → preset de build → despliegue automático según el destino. Los presets universales
  (Debug/Release/RelWithDebInfo/MinSizeRel) siempre están, y además se **auto-descubren** banderas
  extra grepeando el `build.sh` del proyecto activo, o las opciones `option(...)` del propio
  `CMakeLists.txt` si el proyecto no tiene `build.sh` (port legacy) -- así ningún flag específico
  de un motor (NEON, dirty-rect, downsample, turbo, lo que sea) queda hardcodeado en la
  herramienta genérica; simplemente aparece si ESE port lo define.
- ⚡📦 **Subir a la PS Vita física** por FTP -- solo el `eboot.bin` (rápido) o el VPK completo.
- 📥 **Descargar logs / crash dumps** -- tres modos: el último automático, elegir uno específico
  de lo que hay *ahora* en la consola, o navegar el **historial local** de lo ya descargado antes
  a este proyecto.
- 🔍 **Analizar un crash dump** -- wrapper de `vita-parse-core` con resolución automática de
  símbolos, desensamblado alrededor de PC/LR, y reconstrucción de la pila de llamadas.
- 🎨 **LiveArea** -- adapta cualquier PNG a las specs exactas de Vita (bg0/pic0/icon0/startup,
  8-bit indexado, límites de tamaño) con recorte/fit/stretch, directo a `extras/livearea/` del
  proyecto.
- 🧩 **Shaders** -- sincronizar GLSL volcado ↔ CG traducido, limpieza de boilerplate GLES, chequeo
  de `libshacccg.suprx`.
- 🧰 **Utilidades** -- limpieza de basura de macOS, re-decompilación, tests de host del proyecto,
  búsqueda de símbolos por patrón, verificación de assets (local vs. consola), traducción de docs.
- ⚙️ **Configuración del proyecto** / 🔁 **Cambiar de proyecto** / ❌ **Salir**.

Navegación consistente en **todo** el toolkit: `↑/↓` mover, `Enter` elegir, `1-9` salto directo,
`0`/`Q` volver un nivel, **`M` va directo al menú principal del proyecto desde cualquier
submenú**, `Ctrl+C` ídem.

## Estructura

```
psvita_toolkit/
  config.py          # config global + por-proyecto, descubrimiento/adopción de ports
  tui.py             # framework de menú de flechas reutilizable (sin curses)
  project.py         # selector de proyecto (continuar / lista / crear nuevo)
  init_port.py        # asistente "crear port nuevo desde cero"
  build_deploy.py     # asistente de build + despliegue (PS Vita física/local)
  ftp_ops.py          # todo lo que habla FTP con la consola
  livearea.py         # conversor de assets de LiveArea
  crash_analyzer.py   # analizador de .psp2dmp (vita-parse-core)
  utils.py            # limpieza, re-decompilación, tests, símbolos, docs
```

Cada port solo necesita un archivo `.psvita-toolkit.json` en su raíz (auto-generado al crear el
port, o al adoptar uno existente) para que el toolkit sepa operar sobre él. No hace falta ninguna
copia de scripts dentro del repo del port.

## Documentación del código (para mantenedores)

Los comentarios/docstrings del código están en inglés, formato Doxygen (`"""! @brief/@param/@return`),
y separados del racional de diseño ("por qué" -- decisiones, bugs reales encontrados) que vive en
`docs/dev-notes/<módulo>.md`. `dev-tools/gen_docs.py` automatiza la parte mecánica de mantener esto
sin gastar tokens de LLM en cada cambio:

```bash
python3 dev-tools/gen_docs.py --check           # lista símbolos sin docstring (para CI)
python3 dev-tools/gen_docs.py --skeletons-only   # inserta skeletons Doxygen faltantes
python3 dev-tools/gen_docs.py --api-only         # genera docs/api/<módulo>.md (referencia, no versionado)
```

Usa `doxygen`/`doxybook2` si están instalados (`brew install doxygen`); si no, cae a un extractor
propio basado en `ast` (sin dependencias) que da el mismo resultado para este proyecto.
`docs/dev-notes/` sigue siendo 100% escrito a mano -- ninguna herramienta puede generar el "por qué".
