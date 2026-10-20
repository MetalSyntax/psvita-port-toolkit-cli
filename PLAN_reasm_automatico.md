# Plan: capa de re-ensamblado asistido (ARM → C/C++) sobre el corpus de ports

**Estado:** propuesta, no iniciada.
**Fecha:** 2026-09-20.
**Alcance:** `psvita-toolkit` (todos los ports administrados por él), no un port individual.

## 1. Planteamiento original

¿Se puede construir algo como "un Ghidra más preciso", que reensamble automáticamente
el `.so` de Android a C/C++ para PS Vita, sin los vacíos ni errores de interpretación
típicos de un descompilador genérico, aprovechando todo lo ya aprendido en los ports
realizados hasta ahora?

## 2. Veredicto de viabilidad

**No es viable como reemplazo general de un descompilador.** La descompilación a
código fuente fiel y compilable pierde información de forma inherente: tipos,
control de flujo reconstruido desde código optimizado/inlineado, vtables/RTTI/
templates en C++, idioms específicos del compilador. Ni IDA, ni RetDec, ni Ghidra
resuelven esto de forma general — es un problema de investigación abierto, no algo
que se arregle escribiendo más reglas o más scripts. Cualquier plan que prometa
"cero vacíos, 100% automático" está mal planteado desde la base.

**Sí es viable una versión acotada, con ROI real:** no re-descompilar cada `.so`
desde cero, sino que el toolkit **reconozca y reutilice lo que ya se verificó a
mano en ports anteriores**, y aplique de forma sistemática los errores de Ghidra
que ya se conocen por experiencia repetida. Esto es una extensión directa de lo
que `disasm.py` ya hace hoy: ARM/Thumb real vs. pseudo-C de Ghidra, lado a lado,
con el ARM real como única fuente de verdad y el pseudo-C solo como ayuda de
lectura (ver `psvita_toolkit/disasm.py`, sección `disasm.prompt_body`). El cambio
de fondo es llevar eso de "una función a la vez, a demanda, manual" a "el `.so`
completo, en batch, con reutilización de corpus entre ports".

**Techo explícito del plan:** ninguna salida de este sistema se acepta sin
verificación instrucción-por-instrucción contra el ARM real. El objetivo es
reducir drásticamente el volumen de trabajo manual/IA por port, no eliminar la
revisión humana o de IA.

## 3. Fases

### Fase 0 — Corpus

Minar de todos los ports ya hechos:
- `decompiled/*/ghidra/*.c` (pseudo-C de Ghidra).
- `decompiled/disasm/*.md` (bundles ya generados por `disasm.py`).
- `source/patch.c` de cada port (parches ya validados en consola).

Cada función verificada se guarda como un registro:
`hash normalizado de instrucciones` + `nombre/firma` + `C validado que la
reemplaza` + `port/juego de origen` + `fecha de verificación`.

**Entregable:** esquema de datos + script de extracción que recorre los ports
existentes y puebla el corpus inicial.

### Fase 1 — Fingerprinting

Motor de huellas al estilo FLIRT/Diaphora: hash de la secuencia de opcodes
normalizada (ignorando inmediatos que cambian por relocation — direcciones de
literal pool, offsets de PLT) para reconocer una función aunque esté en otra
dirección o en otro binario. Gran parte de un `.so` de Android suele ser
boilerplate de compilador/STL/motor ya visto en otro port — esta fase es la de
mayor ROI porque no depende de acumular muchos ports propios: se puede sembrar
con librerías conocidas (misma versión de NDK/libc++, mismos SDKs de motor)
usadas en varios de los ports ya hechos.

**Entregable:** extensión de `_load_full_symbols()` / `_disassemble_range()` en
`psvita_toolkit/disasm.py` con normalización de instrucciones + índice de
huellas consultable.

### Fase 2 — Linter de discrepancias Ghidra vs. ARM real

Convertir los patrones de error de Ghidra ya identificados a mano (idioms de
Thumb-2 mal traducidos, huecos conocidos) en reglas explícitas, minadas de las
notas de `so-crash-triage` y de los `FIELD_REPORT_*.md` existentes (p. ej.
`docs/FIELD_REPORT_asphalt5-vita_2026-08-31.md`), y correrlas sobre el pseudo-C
completo cruzado contra el disassembly real.

**Entregable:** modo batch de `_find_ghidra_snippet()` que recorra todo
`decompiled/*/ghidra/*.c` de un port, no una función a demanda.

### Fase 3 — Pipeline batch por `.so`

Recorrer todas las funciones vía la tabla de símbolos (`_load_full_symbols`),
clasificar cada una:
- (a) match exacto/casi-exacto en el corpus → sustituir directo por el C ya
  validado.
- (b) match parcial → marcar como candidata de alta confianza para revisión
  rápida.
- (c) sin match → cola de revisión manual/IA, priorizada.

**Entregable:** subcomando nuevo (o modo de `disasm.py`) que genera un reporte
de clasificación por `.so` completo en vez de por función.

### Fase 4 — Relleno asistido por IA para lo genuinamente nuevo

Para el bucket (c): mantener la misma filosofía que ya está en el prompt de
`disasm.py` — el ARM real es la única fuente de verdad, el pseudo-C es solo
ayuda de lectura, cualquier propuesta de la IA se verifica instrucción por
instrucción antes de aceptarse. Nunca autocompilar sin ese paso.

**Entregable:** integración con el flujo existente de `disasm_menu()` /
context feeding hacia el asistente de IA, sin cambios de filosofía, solo de
volumen (ya viene pre-filtrado por las fases 1-3).

### Fase 5 — Ciclo de retroalimentación

Cada función verificada en cada port futuro se agrega de vuelta al corpus
(Fase 0). El sistema mejora de forma medible port a port en vez de depender de
que "aprenda solo".

**Entregable:** hook de fin-de-port (o comando manual) que vuelca las funciones
recién validadas de ese port al corpus compartido.

## 4. Qué se necesita

- **Almacenamiento del corpus:** `psvita_toolkit/data/corpus/` si se mantiene
  dentro de este repo, o un repo separado si el corpus crece mucho — evitar que
  quede atado a un solo port o se mezcle con el código de un juego específico
  (riesgo DMCA si el corpus llegara a incluir código derivado de un binario
  propietario sin cuidado; el corpus debe guardar huellas/hashes y C ya escrito
  por el usuario, no fragmentos crudos del binario original).
- **Extensión de `disasm.py`:** modo batch + hashing normalizado de
  instrucciones (ya existen `_load_full_symbols` y `_disassemble_range`, falta
  la normalización y el índice de huellas).
- **Semilla inicial del corpus:** hashear librerías conocidas que ya aparecieron
  en varios ports (misma versión de NDK/libc++, mismos SDKs de motor) para tener
  matches desde el día uno.
- **Reglas del linter (Fase 2):** escritas a partir de las notas de
  `so-crash-triage` y los `FIELD_REPORT_*.md` ya existentes.

## 5. Orden recomendado de ejecución

Empezar solo por **Fase 1 + Fase 2** (fingerprinting + linter): no dependen de
tener un corpus grande todavía y ya reducen el trabajo manual actual. El resto
(Fases 0, 3, 4, 5 como pipeline integrado) se construye encima una vez que eso
esté probado en un port real.

## 6. Próximo paso

Pendiente de decisión del usuario: arrancar por Fase 1 (extender `disasm.py`
con hashing normalizado + modo batch) o por Fase 2 (linter de discrepancias a
partir de las notas de triage existentes).
