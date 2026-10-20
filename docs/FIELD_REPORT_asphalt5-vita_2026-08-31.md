# Reporte de campo: sesión de diagnóstico de GPU en Asphalt-5-Vita (2026-08-31)

> Generado a partir de una sesión real de uso del toolkit contra un port existente (`Asphalt-5-Vita`),
> no de teoría. Cada item de abajo tiene una causa raíz confirmada (código fuente leído, comportamiento
> reproducido) -- no son sugerencias especulativas. Pensado para alimentar una futura pasada de mejoras
> (un eventual PLAN V6), no para implementarse a ciegas sin releer el módulo correspondiente primero.

## Contexto

El port llegaba al menú pero colapsaba a los pocos segundos de carrera ("GPU crash"). Se usó
`perf-telemetry --gen-hooks` para instrumentar, se descubrieron 2 bugs reales en el código
autogenerado, se hookearon funciones de motor específicas (`Scene::Update`/`Render`/etc, técnica no
cubierta por el generador actual) para localizar la fase exacta, y finalmente se usó `analyze` +
lectura directa del dump para determinar que el "GPU crash" no era un fault real. La causa raíz
terminó estando en `vitaGL` (compresor DXT por software bloqueando el hilo), no en el juego.
Ver `Asphalt-5-Vita/port_progress.md` (Bugs #22/#23 y la sesión de vitaGL) para el detalle completo.

---

## 1. `perf_telemetry.py` -- 2 bugs reales en el código C generado (no solo "verificar antes de usar")

El header/comentarios generados ya avisan "verify against your vitasdk headers", pero en la práctica
encontré 2 casos donde el código generado **no compila o compila mal contra un VITASDK real**, no solo
"podría estar desactualizado":

- **`sceKernelGetProcessTimeWide()` se generó como si tomara un out-pointer**
  (`sceKernelGetProcessTimeWide(&x)`), pero la firma real (confirmada en
  `psp2/kernel/processmgr.h` de este vitasdk) es `SceUInt64 sceKernelGetProcessTimeWide(void)` --
  devuelve el valor directo. Esto es un error de compilación duro (`too many arguments`), no un
  "podría fallar en tu versión". Corregir la plantilla a `SceUInt64 x = sceKernelGetProcessTimeWide();`.
- **`perf_telemetry_sample_cores()` asume una API que no existe**: un snapshot global de qué hilo
  corre en cada uno de los 4 cores. La función real, `sceKernelGetThreadRunStatus(SceUID thid,
  SceKernelThreadRunStatus *status)`, es **por-hilo** (necesita un `SceUID` específico), no un
  snapshot de 4 cores, y su struct tiene campos `processId`/`threadId`/`priority` por entrada de
  `cpuInfo[4]`, no `currentThreadId`. La función generada no es "verificar el nombre de un campo" --
  es una API inventada. Recomendación: quitar esta función del generador (el propio comentario ya dice
  "delete if it doesn't compile"), o rehacerla como lo que realmente se puede hacer con esa API
  (reportar prioridad/processId por hilo dado un `SceUID` conocido, no un snapshot de 4 cores).
- **Los hooks generados abren su propio socket BSD crudo** (`<sys/socket.h>`/`<netinet/in.h>`) en vez
  de reusar el sink UDP que el proyecto ya podría tener (muchos ports vía este toolkit tienen su propio
  `netlog.c`-estilo basado en `sceNet`, no BSD sockets). No sé si BSD sockets funcionan de verdad en
  Vita vía vitasdk (no lo probé, terminé reemplazándolo), pero en cualquier caso abrir un SEGUNDO canal
  UDP cuando el proyecto ya tiene uno es trabajo/superficie de bug innecesaria. Sugerencia: que el
  generador pregunte/detecte si ya existe un sink de log UDP en el proyecto (buscar `netlog.c`/similar)
  y en ese caso genere código que lo reuse en vez de un socket propio.
- **Las líneas generadas no llevan `\n`** -- el `nc -u -l` receptor mostró todo el stream de telemetría
  pegado en una sola línea larga sin separadores, porque `pt_send()` no agrega salto de línea (a
  diferencia de, por ejemplo, cómo cualquier logger normal sí lo hace). Bug de una línea en la
  plantilla, pero hace que el output generado sea inútil hasta que se nota.
- **Relacionado, no un bug pero sí una limitación real encontrada en uso:** con `%zu` en el `snprintf`
  de este toolchain vitasdk específico, el conversor se imprime literal (`"zu"`) en vez del valor --
  sin ningún error de compilación. El generador ya usa `%llu` en `FRAME`/`GPU`; sería bueno que TODO
  el código generado evite `%z` por consistencia con ese mismo toolchain, ya que el propio generador
  demuestra conocer el problema en un lugar pero no en otro.

## 2. Falta un modo de "bracket" por función/símbolo específico, no solo frame/GPU global

Lo que realmente encontró la causa raíz no fueron las métricas de FRAME/GPU genéricas, sino hookear
funciones de motor puntuales (`Scene::Update`/`UpdateCars`/`Render`/`RenderInterface`,
`gxRenderGroup::RenderGroups`, `Scene::RenderCars`) vía `hook_addr()`+`SO_CONTINUE` (mecanismo ya
provisto por `so_util`, usado en este port para `CMatrix::Mult`), emitiendo `PHASE_ENTER`/`PHASE_EXIT`
antes/después de cada una. Esto:

- Subdivide un frame lento en "cuál de las N fases fue" en vez de solo "el frame fue lento".
- Si la consola se cuelga a mitad de una fase, la línea `PHASE_ENTER` sin su `PHASE_EXIT`
  correspondiente en el receptor UDP dice exactamente qué función estaba corriendo -- sin esperar
  un `.psp2dmp`.

Esto no existe hoy en `perf_telemetry.py` (`--gen-hooks` sólo da FRAME/GPU/cores). Sería una mejora de
alto valor: dado el nombre mangled de una o más funciones (via `nm`/símbolos ya conocidos por el
proyecto), generar automáticamente el par `hook_<símbolo>`/registro en `so_patch()` con
`PHASE_ENTER`/`PHASE_EXIT`, en vez de que cada sesión lo escriba a mano como pasó acá. El código C que
escribí a mano en `Asphalt-5-Vita/source/patch.c` (buscar "Sub-phases inside Scene::Render") sirve de
referencia concreta del patrón.

## 3. `crash_analyzer.py` -- un dump etiquetado "-GPUCRASH" puede no ser un fault real, y el reporte no lo dice

El dump `asphalt5-psp2core-1788151414-GPUCRASH.psp2dmp` (el sufijo `GPUCRASH` lo pone algo externo al
toolkit -- no confirmé qué proceso en la consola nombra así los archivos) tenía **los 9 hilos con
`stop_reason == 0x0`** -- confirmado no solo por el reporte de `analyze`, sino re-leyendo el dump
directamente con `core.CoreParser` y listando `stop_reason` de cada hilo a mano. La lógica actual en
`crash_analyzer.py` (`crashed = [t for t in core.threads if t.stop_reason != 0] or core.threads[:1]`)
ya maneja este caso correctamente puertas adentro (cae a reportar el hilo #1 si ninguno tiene un
`stop_reason` real), pero **el reporte impreso no distingue este caso de un crash real** -- imprime
igual "HILO EN CRASH" + "Razón de parada: No reason (ejecución normal)" sin ninguna advertencia de que
esto probablemente NO es un fault de CPU sino un dump forzado por un watchdog externo sobre un proceso
que seguía vivo y corriendo normal. Sugerencia concreta: cuando `crashed` viene del fallback (ningún
hilo real con `stop_reason != 0`), agregar una línea explícita tipo "⚠ Ningún hilo muestra una razón de
parada real -- este dump probablemente representa un stall/watchdog, no un fault de CPU" en vez de
dejar que el lector infiera esto del código 0x0 solo.

## 4. Gotcha real (no bug del toolkit, pero vale la pena que `doctor`/docs lo adviertan): `.gitignore` sin anclar + submódulos vendorizados + `build_deploy.py`

`_stage_in_tmp()` usa `rsync --filter=":- .gitignore"` para copiar el proyecto a un directorio
temporal antes de compilar (para no arrastrar carpetas gigantes gitignoreadas). Si el `.gitignore` del
proyecto tiene una regla sin anclar tipo `Makefile` (pensada para ignorar el `Makefile` que genera
CMake), esa MISMA regla también borra `lib/<algo>/Makefile` -- un archivo fuente real y necesario de
CUALQUIER submódulo git vendorizado que traiga su propio Makefile (pasó acá con `lib/vitagl`, vendorizado
para poder compilarlo con flags propias en vez de usar el paquete precompilado de `vdpm` -- ver
`Asphalt-5-Vita/CMakeLists.txt` y su commit "Vendor vitaGL from source..."). El síntoma en consola es
un genérico `make: *** No targets specified and no makefile found`, sin ninguna pista de que la causa
es el `.gitignore`, no el submódulo ni el CMakeLists. Encontrar la causa real tomó leer
`build_deploy.py` línea por línea.

Sugerencia: `doctor` (o un chequeo nuevo, liviano) podría detectar este patrón -- para cada submódulo
git bajo `lib/` (o donde sea) que tenga su propio `Makefile`/`CMakeLists.txt`, verificar con
`git check-ignore -v` si ese archivo específico quedaría excluido por el `.gitignore` del proyecto
padre, y avisar ANTES de que el build falle con un error genérico. Este patrón (vendorizar una librería
de terceros para compilarla con flags propias) ya se repite en al menos 2 ports en esta misma máquina
(`asphalt8-vita-main` con `lib/vitagl`, y ahora `Asphalt-5-Vita`), así que no es un caso de un solo
proyecto.

## 5. No hay un subcomando genérico de disassembly/símbolos -- sólo `analyze` (específico de `.psp2dmp`)

Para "conocer todas las funciones del motor" (pedido explícito del usuario, sin relación con ningún
crash puntual) tuve que invocar `arm-vita-eabi-objdump`/`nm` a mano contra el `.so` del juego. Un
subcomando tipo `psvita-toolkit disasm <so.so> [--out <archivo>]` / `psvita-toolkit symbols <so.so>
[--sort addr|size] [--demangle]` (wrappers finos sobre esas mismas herramientas del VITASDK, con la
base de carga del proyecto ya conocida vía `.psvita-toolkit.json` para anotar direcciones absolutas)
sería un complemento natural a `analyze`, reusando la misma infraestructura de símbolos que ya usa
`crash_analyzer.py`/`context_feeder.py`.

## 6. No hay una receta/generador para "vendorizar y compilar una librería C de terceros con flags propias"

El patrón "agregar `lib/<nombre>` como submódulo git + compilarlo con su propio Makefile vía
`add_custom_target` en vez de linkear el paquete precompilado de `vdpm`" tuve que reconstruirlo a mano
leyendo el `CMakeLists.txt` de otro port (`asphalt8-vita-main`) línea por línea (el bloque
`VITAGL_MAKE_FLAGS`/stamp file/`add_library(... STATIC IMPORTED)`). Si esto se repite entre ports (ya
van 2 en esta máquina), un snippet documentado (o generado) en el toolkit -- "cómo vendorizar y
compilar `vitaGL` (u otra lib) desde fuente con flags custom, en vez de usar el paquete de `vdpm`" --
ahorraría reconstruir este patrón cada vez, incluyendo las 3 incompatibilidades reales que encontré al
compilar el `master` actual de vitaGL contra un vitasdk más viejo (constantes de `gxm.h` faltantes,
colisión de símbolos EGL contra un `reimpl/egl.c` propio, una función sin guardar que necesita una
librería de devkit) -- documentadas en `Asphalt-5-Vita/lib/vitagl`'s propio commit local, útiles como
referencia si esto se vuelve a hacer en otro port.
