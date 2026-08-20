"""!
@file catalog.py
@brief Human-facing catalog of every capability this toolkit has -- what the
       web dashboard's LiveArea/Performance tabs and this module's own
       `psvita-toolkit tools` output both describe, but from the terminal.

@details
This toolkit grew to 27 tools across a dozen modules over several rounds of
work; the terminal is where a porter actually lives day to day, so the same
"what does each one actually do" reference that exists as a web artifact
also needs to be reachable without leaving the TUI/CLI. `CATALOG` is the one
source both read from -- grouped the same way the web version is (by where
a tool enters the porting workflow, not by which file implements it), so
the two stay in sync by construction instead of by manual upkeep.

Descriptions are full `{"es", "en", "pt"}` dicts like every other
user-facing string in this toolkit (`print_catalog()` reads
`i18n.get_language()` the same way `t()` does), NOT run through
`i18n.register()`/`t()` individually -- there's no single lookup key per
tool the way there is for a UI label; the group/tool text is looked up
directly by language code. See `docs/dev-notes/catalog.md`.
"""

from . import i18n
from . import tui
from .i18n import t
from .tui import C

STRINGS = {
    "catalog.menu_title": {
        "es": "Catálogo de Herramientas",
        "en": "Tool Catalog",
        "pt": "Catálogo de Ferramentas",
    },
    "catalog.header": {
        "es": "psvita-toolkit -- qué hace cada herramienta",
        "en": "psvita-toolkit -- what each tool does",
        "pt": "psvita-toolkit -- o que cada ferramenta faz",
    },
    "catalog.footer": {
        "es": "{count} herramientas -- un solo comando: psvita-toolkit <subcomando>. 'psvita-toolkit doctor' si algo no anda.",
        "en": "{count} tools -- one command: psvita-toolkit <subcommand>. 'psvita-toolkit doctor' if something's off.",
        "pt": "{count} ferramentas -- um só comando: psvita-toolkit <subcomando>. 'psvita-toolkit doctor' se algo não funcionar.",
    },
    "catalog.tag.real_console": {
        "es": "consola real",
        "en": "real console",
        "pt": "console real",
    },
    "catalog.tag.gen_c": {
        "es": "genera C",
        "en": "generates C",
        "pt": "gera C",
    },
    "catalog.tag.optional": {
        "es": "herramienta externa opcional",
        "en": "optional external tool",
        "pt": "ferramenta externa opcional",
    },
}
i18n.register(STRINGS)

_REAL_CONSOLE = "catalog.tag.real_console"
_GEN_C = "catalog.tag.gen_c"
_OPTIONAL = "catalog.tag.optional"

CATALOG = [
    {
        "title": {"es": "Diagnóstico y arranque", "en": "Diagnostics & bootstrap", "pt": "Diagnóstico e inicialização"},
        "desc": {
            "es": "Lo primero que se corre en una máquina nueva, y lo que crea un port desde cero.",
            "en": "The first thing to run on a new machine, and what creates a port from scratch.",
            "pt": "A primeira coisa a rodar em uma máquina nova, e o que cria um port do zero.",
        },
        "tools": [
            {"name": "doctor", "tags": [], "desc": {
                "es": "Revisa que VITASDK, Docker, jadx, CMake/Ninja, gdb-multiarch, glslangValidator/spirv-cross y los paquetes de Python estén instalados, y dice exactamente qué falta y cómo instalarlo.",
                "en": "Checks that VITASDK, Docker, jadx, CMake/Ninja, gdb-multiarch, glslangValidator/spirv-cross, and the Python packages are installed, and says exactly what's missing and how to install it.",
                "pt": "Verifica se VITASDK, Docker, jadx, CMake/Ninja, gdb-multiarch, glslangValidator/spirv-cross e os pacotes Python estão instalados, e diz exatamente o que falta e como instalar.",
            }},
            {"name": "init", "tags": [], "desc": {
                "es": "Toma un .apk de Android, detecta ABI y GLES, decompila con jadx + Ghidra, y deja un repo git nuevo con PORTING_PLAN.md listo para empezar a portear.",
                "en": "Takes an Android .apk, detects ABI and GLES, decompiles with jadx + Ghidra, and leaves a fresh git repo with PORTING_PLAN.md ready to start porting.",
                "pt": "Recebe um .apk Android, detecta ABI e GLES, decompila com jadx + Ghidra, e deixa um repo git novo com PORTING_PLAN.md pronto para começar a portar.",
            }},
            {"name": {"es": "selector de proyecto", "en": "project selector", "pt": "seletor de projeto"}, "tags": [], "desc": {
                "es": "Al abrir el TUI: seguir con el último port, elegir otro de los que hay bajo tu carpeta base, o adoptar uno viejo detectando IP/TITLEID automáticamente.",
                "en": "On opening the TUI: continue with the last port, pick another one from your base folder, or adopt an old one with IP/TITLEID auto-detected.",
                "pt": "Ao abrir o TUI: continuar com o último port, escolher outro da sua pasta base, ou adotar um antigo detectando IP/TITLEID automaticamente.",
            }},
        ],
    },
    {
        "title": {"es": "Compilar y desplegar", "en": "Build and deploy", "pt": "Compilar e implantar"},
        "desc": {
            "es": "De código fuente a algo instalado en la consola.",
            "en": "From source code to something installed on the console.",
            "pt": "Do código-fonte a algo instalado no console.",
        },
        "tools": [
            {"name": "build", "tags": [], "desc": {
                "es": "Compila con build.sh si el proyecto lo tiene; si no, cae a CMake + Ninja directo con el toolchain de Vita. Detecta flags específicas del motor sin que estén hardcodeadas en la herramienta.",
                "en": "Builds with build.sh if the project has one; otherwise falls back to direct CMake + Ninja with the Vita toolchain. Detects engine-specific flags without them being hardcoded in the tool.",
                "pt": "Compila com build.sh se o projeto tiver um; senão cai para CMake + Ninja direto com o toolchain da Vita. Detecta flags específicas do motor sem que estejam fixas na ferramenta.",
            }},
            {"name": "deploy", "tags": [_REAL_CONSOLE], "desc": {
                "es": "Sube por FTP solo el eboot.bin para iterar rápido, o el .vpk completo -- con reconexión automática y perfiles guardados por consola (OLED / Slim / PSTV).",
                "en": "Uploads over FTP just eboot.bin to iterate fast, or the full .vpk -- with automatic reconnection and saved per-console profiles (OLED / Slim / PSTV).",
                "pt": "Envia por FTP só o eboot.bin para iterar rápido, ou o .vpk completo -- com reconexão automática e perfis salvos por console (OLED / Slim / PSTV).",
            }},
        ],
    },
    {
        "title": {"es": "LiveArea y shaders", "en": "LiveArea and shaders", "pt": "LiveArea e shaders"},
        "desc": {
            "es": "Lo que ve el usuario antes de jugar, y lo que la GPU necesita para dibujar.",
            "en": "What the user sees before playing, and what the GPU needs to draw.",
            "pt": "O que o usuário vê antes de jogar, e o que a GPU precisa para desenhar.",
        },
        "tools": [
            {"name": "livearea", "tags": [], "desc": {
                "es": "Convierte tus PNG a las specs exactas de Vita (8-bit indexado, límites de KB), genera template.xml y bgm.at9, y valida todo antes de empaquetar.",
                "en": "Converts your PNGs to Vita's exact specs (8-bit indexed, KB limits), generates template.xml and bgm.at9, and validates everything before packaging.",
                "pt": "Converte seus PNGs para as specs exatas da Vita (8-bit indexado, limites de KB), gera template.xml e bgm.at9, e valida tudo antes de empacotar.",
            }},
            {"name": {"es": "vista previa de LiveArea", "en": "LiveArea preview", "pt": "pré-visualização de LiveArea"}, "tags": [_REAL_CONSOLE], "desc": {
                "es": "Muestra bg0/pic0/icon0/startup lado a lado a la misma escala real, en el dashboard web -- para pescar un asset mal recortado antes de gastar un ciclo de build.",
                "en": "Shows bg0/pic0/icon0/startup side by side at the same real scale, in the web dashboard -- to catch a badly-cropped asset before spending a build cycle.",
                "pt": "Mostra bg0/pic0/icon0/startup lado a lado na mesma escala real, no dashboard web -- para pegar um asset mal recortado antes de gastar um ciclo de build.",
            }},
            {"name": "shader-transpile", "tags": [_OPTIONAL], "desc": {
                "es": "Traduce shaders GLSL a Cg con un pipeline real de AST (glslangValidator -> SPIR-V -> spirv-cross), no expresiones regulares -- valida cada resultado con psp2cgc.",
                "en": "Translates GLSL shaders to Cg with a real AST pipeline (glslangValidator -> SPIR-V -> spirv-cross), not regexes -- validates every result with psp2cgc.",
                "pt": "Traduz shaders GLSL para Cg com um pipeline real de AST (glslangValidator -> SPIR-V -> spirv-cross), não expressões regulares -- valida cada resultado com psp2cgc.",
            }},
            {"name": "shader-live-reload", "tags": [_REAL_CONSOLE], "desc": {
                "es": "Vigila assets/cg/ y sube cada shader por FTP apenas lo guardás, ya validado -- el ciclo editar-probar sin abrir un menú a mano.",
                "en": "Watches assets/cg/ and uploads each shader over FTP the moment you save it, already validated -- the edit-test loop without opening a menu by hand.",
                "pt": "Vigia assets/cg/ e envia cada shader por FTP no momento em que você salva, já validado -- o ciclo editar-testar sem abrir um menu manualmente.",
            }},
        ],
    },
    {
        "title": {"es": "Depuración y triage de crashes", "en": "Debugging and crash triage", "pt": "Depuração e triagem de crashes"},
        "desc": {
            "es": "Cuando el juego se cuelga y hay que averiguar por qué, en la consola de verdad.",
            "en": "When the game crashes and you need to know why, on the real console.",
            "pt": "Quando o jogo trava e é preciso descobrir por quê, no console de verdade.",
        },
        "tools": [
            {"name": "analyze", "tags": [], "desc": {
                "es": "Lee un .psp2dmp, resuelve símbolos del .so, y cruza automáticamente la dirección del crash contra el pseudo-código de Ghidra y el Java de jadx.",
                "en": "Reads a .psp2dmp, resolves the .so's symbols, and automatically cross-references the crash address against Ghidra's pseudo-code and jadx's Java.",
                "pt": "Lê um .psp2dmp, resolve os símbolos do .so, e cruza automaticamente o endereço do crash com o pseudocódigo do Ghidra e o Java do jadx.",
            }},
            {"name": "logs-live", "tags": [_REAL_CONSOLE], "desc": {
                "es": "Servidor de logs UDP en vivo, coloreado por severidad -- para cuelgues tan totales que ni llegan a escribir el log a disco antes de que resetees la consola.",
                "en": "Live UDP log server, colored by severity -- for hangs so total the game never gets to write the log to disk before you reset the console.",
                "pt": "Servidor de logs UDP em tempo real, colorido por severidade -- para hangs tão totais que o jogo nem chega a escrever o log em disco antes de você resetar o console.",
            }},
            {"name": "gdb-map", "tags": [_OPTIONAL], "desc": {
                "es": "Genera el script para gdb-multiarch con los símbolos del loader y del .so Android, contra el gdbstub que ya tenga tu loader. Puede capturar solo la dirección base por UDP.",
                "en": "Generates the gdb-multiarch script with the loader's and the Android .so's symbols, against whatever gdbstub your loader already has. Can auto-capture the base address over UDP.",
                "pt": "Gera o script para gdb-multiarch com os símbolos do loader e do .so Android, contra o gdbstub que seu loader já tiver. Pode capturar sozinho o endereço base por UDP.",
            }},
            {"name": "export-context", "tags": [], "desc": {
                "es": "Empaqueta el crash, el pseudo-código relevante de Ghidra, el Java de jadx y el stub actual en un solo documento -- listo para pegar en un chat de IA.",
                "en": "Packages the crash, the relevant Ghidra pseudo-code, jadx's Java, and the current stub into one document -- ready to paste into an AI chat.",
                "pt": "Empacota o crash, o pseudocódigo relevante do Ghidra, o Java do jadx e o stub atual em um único documento -- pronto para colar em um chat de IA.",
            }},
        ],
    },
    {
        "title": {"es": "Ingeniería inversa y auto-parcheo", "en": "Reverse engineering and auto-patching", "pt": "Engenharia reversa e auto-patch"},
        "desc": {
            "es": "Lo que hay que entender y neutralizar del binario de Android antes de que arranque.",
            "en": "What needs to be understood and neutralized in the Android binary before it boots.",
            "pt": "O que é preciso entender e neutralizar no binário Android antes de ele iniciar.",
        },
        "tools": [
            {"name": "jni-analyze", "tags": [_GEN_C], "desc": {
                "es": "Detecta middleware conocido (FMOD, OpenAL, Unity, Cocos2d...) y genera candidatos de stubs de FalsoJNI a partir de los métodos nativos del .so.",
                "en": "Detects known middleware (FMOD, OpenAL, Unity, Cocos2d...) and generates FalsoJNI stub candidates from the .so's native methods.",
                "pt": "Detecta middleware conhecido (FMOD, OpenAL, Unity, Cocos2d...) e gera candidatos de stubs de FalsoJNI a partir dos métodos nativos do .so.",
            }},
            {"name": "so-patch", "tags": [_GEN_C], "desc": {
                "es": "Detecta Google Play Services, AdMob, Firebase y rutas hardcodeadas de Android; genera stubs de neutralización, y puede aplicar un parche binario real (retorno seguro) en el .so en disco, con backup y reversión.",
                "en": "Detects Google Play Services, AdMob, Firebase, and hardcoded Android paths; generates neutralization stubs, and can apply a real binary patch (safe return) to the .so file on disk, with backup and revert.",
                "pt": "Detecta Google Play Services, AdMob, Firebase e caminhos hardcoded do Android; gera stubs de neutralização, e pode aplicar um patch binário real (retorno seguro) no .so em disco, com backup e reversão.",
            }},
            {"name": "align-check", "tags": [], "desc": {
                "es": "Busca instrucciones ARM sensibles a alineación (ldrd, vld1...) que pasan piolas en Android pero explotan en el Cortex-A9 de Vita, y documenta el riesgo.",
                "en": "Looks for alignment-sensitive ARM instructions (ldrd, vld1...) that slide by on Android but blow up on Vita's Cortex-A9, and documents the risk.",
                "pt": "Procura instruções ARM sensíveis a alinhamento (ldrd, vld1...) que passam batido no Android mas explodem no Cortex-A9 da Vita, e documenta o risco.",
            }},
        ],
    },
    {
        "title": {"es": "Consola real", "en": "Real console", "pt": "Console real"},
        "desc": {
            "es": "Nada de esto pasa por un emulador -- se removió Vita3K del toolkit al confirmarlo incompatible. Todo lo de abajo habla con hardware real por FTP o UDP.",
            "en": "None of this goes through an emulator -- Vita3K was removed from the toolkit after confirming it's incompatible. Everything below talks to real hardware over FTP or UDP.",
            "pt": "Nada disso passa por um emulador -- o Vita3K foi removido do toolkit após confirmar que é incompatível. Tudo abaixo fala com hardware real por FTP ou UDP.",
        },
        "tools": [
            {"name": "mem-profile", "tags": [_REAL_CONSOLE, _GEN_C], "desc": {
                "es": "Heap en vivo por UDP: bloques vivos, bytes en uso, y candidatos a fuga de memoria relativos a un checkpoint de nivel/escena.",
                "en": "Live heap over UDP: live blocks, bytes in use, and leak candidates relative to a level/scene checkpoint.",
                "pt": "Heap em tempo real por UDP: blocos vivos, bytes em uso, e candidatos a fuga de memória relativos a um checkpoint de nível/cena.",
            }},
            {"name": "perf-telemetry", "tags": [_REAL_CONSOLE, _GEN_C], "desc": {
                "es": "FPS, p95 de frame time y stutters en vivo, más el tiempo real que tarda sceGxmFinish() como señal genuina de trabajo de GPU -- sin inventar un contador que Vita no expone.",
                "en": "Live FPS, frame-time p95, and stutters, plus the real time sceGxmFinish() takes as a genuine GPU-work signal -- without fabricating a counter Vita doesn't expose.",
                "pt": "FPS, p95 de frame time e stutters em tempo real, além do tempo real que sceGxmFinish() leva como sinal genuíno de trabalho de GPU -- sem inventar um contador que a Vita não expõe.",
            }},
            {"name": "soak-test", "tags": [_REAL_CONSOLE], "desc": {
                "es": "Heartbeat por UDP para pescar hangs (no solo crashes) en corridas largas sin supervisión, certificando \"0 incidentes\" solo cuando es cierto. Se puede correr junto al profiler de memoria.",
                "en": "UDP heartbeat to catch hangs (not just crashes) on long unattended runs, certifying \"0 incidents\" only when it's true. Can run alongside the memory profiler.",
                "pt": "Heartbeat por UDP para pegar hangs (não só crashes) em execuções longas sem supervisão, certificando \"0 incidentes\" só quando é verdade. Pode rodar junto com o profiler de memória.",
            }},
            {"name": "auto-bootstrap", "tags": [_REAL_CONSOLE], "desc": {
                "es": "Compila, despliega, espera, y si la consola larga un crash nuevo regenera candidatos de stub y reintenta -- se detiene solo si no hay progreso, nunca confía en un fix sin revisar.",
                "en": "Builds, deploys, waits, and if the console throws a new crash it regenerates stub candidates and retries -- stops on its own if there's no progress, never trusts an unreviewed fix.",
                "pt": "Compila, implanta, espera, e se o console gerar um crash novo regenera candidatos de stub e tenta de novo -- para sozinho se não houver progresso, nunca confia em um fix sem revisão.",
            }},
            {"name": "transcode-assets", "tags": [_GEN_C, _OPTIONAL], "desc": {
                "es": "Texturas a un contenedor propio con mipmaps precalculados (más compresión GPU real si hay PVRTexToolCLI), audio a .at9 en lote, y el código C que efectivamente los carga.",
                "en": "Textures into a self-documented container with precomputed mipmaps (plus real GPU compression if PVRTexToolCLI is present), audio to .at9 in bulk, and the C code that actually loads them.",
                "pt": "Texturas para um contêiner próprio com mipmaps pré-calculados (mais compressão GPU real se houver PVRTexToolCLI), áudio para .at9 em lote, e o código C que efetivamente os carrega.",
            }},
            {"name": "web", "tags": [_REAL_CONSOLE], "desc": {
                "es": "Dashboard local en el navegador: logs en vivo, estado de la consola, crash dumps, assets de LiveArea, mapeador visual Touch-to-Pad, y el gráfico de rendimiento -- todo en una pestaña.",
                "en": "Local browser dashboard: live logs, console status, crash dumps, LiveArea assets, the visual Touch-to-Pad mapper, and the performance graph -- all in one tab.",
                "pt": "Dashboard local no navegador: logs em tempo real, status do console, crash dumps, assets de LiveArea, mapeador visual Touch-to-Pad, e o gráfico de desempenho -- tudo em uma aba.",
            }},
            {"name": {"es": "mapeador Touch-to-Pad", "en": "Touch-to-Pad mapper", "pt": "mapeador Touch-to-Pad"}, "tags": [_GEN_C], "desc": {
                "es": "Dibujás las zonas táctiles sobre una captura del juego original y exporta touch_bindings.c ya escalado a las unidades reales del panel táctil de Vita.",
                "en": "You draw the touch zones over a screenshot of the original game and it exports touch_bindings.c already scaled to Vita's real touch-panel units.",
                "pt": "Você desenha as zonas de toque sobre uma captura do jogo original e exporta touch_bindings.c já escalado para as unidades reais do painel touch da Vita.",
            }},
        ],
    },
    {
        "title": {"es": "Ecosistema y utilidades", "en": "Ecosystem and utilities", "pt": "Ecossistema e utilitários"},
        "desc": {
            "es": "Para cuando hay más de un port, y para el mantenimiento del toolkit mismo.",
            "en": "For when there's more than one port, and for maintaining the toolkit itself.",
            "pt": "Para quando há mais de um port, e para a manutenção do próprio toolkit.",
        },
        "tools": [
            {"name": "ecosystem-status / sync-shared", "tags": [], "desc": {
                "es": "Vista de pájaro de todos tus ports (progreso, LiveArea, shaders pendientes) y propagación de componentes compartidos entre ports del mismo motor con un solo comando.",
                "en": "Bird's-eye view of all your ports (progress, LiveArea, pending shaders) and propagating shared components across ports of the same engine with one command.",
                "pt": "Visão geral de todos os seus ports (progresso, LiveArea, shaders pendentes) e propagação de componentes compartilhados entre ports do mesmo motor com um só comando.",
            }},
            {"name": "clean-junk", "tags": [], "desc": {
                "es": "Borra los archivos ._* que macOS deja en discos externos no-HFS+ -- la razón por la que un build a veces falla por un archivo que ni sabías que existía.",
                "en": "Deletes the ._* files macOS leaves on non-HFS+ external drives -- the reason a build sometimes fails over a file you didn't even know existed.",
                "pt": "Apaga os arquivos ._* que o macOS deixa em discos externos não-HFS+ -- a razão pela qual um build às vezes falha por um arquivo que você nem sabia que existia.",
            }},
            {"name": {"es": "búsqueda de símbolos / traducción de docs", "en": "symbol search / doc translation", "pt": "busca de símbolos / tradução de docs"}, "tags": [], "desc": {
                "es": "Buscar un patrón entre los símbolos dinámicos de todos los .so del proyecto, o traducir en lote la documentación del port a otro idioma.",
                "en": "Search a pattern across all the project's .so dynamic symbols, or batch-translate the port's documentation to another language.",
                "pt": "Buscar um padrão entre os símbolos dinâmicos de todos os .so do projeto, ou traduzir em lote a documentação do port para outro idioma.",
            }},
            {"name": "gen_docs", "tags": [], "desc": {
                "es": "Mantiene los skeletons de Doxygen y la referencia en docs/api/ al día sin gastar tokens de LLM en cada cambio de código.",
                "en": "Keeps the Doxygen skeletons and the docs/api/ reference up to date without spending LLM tokens on every code change.",
                "pt": "Mantém os skeletons do Doxygen e a referência em docs/api/ atualizados sem gastar tokens de LLM em cada mudança de código.",
            }},
        ],
    },
]


def _tool_name(tool):
    """!
    @brief Resolve a `CATALOG` entry's display name -- most tool names are
           literal CLI subcommands (language-independent), a few are
           described in prose instead (e.g. "project selector") and need
           the same `{"es", "en", "pt"}` lookup as every description.
    @param tool One entry from a group's `"tools"` list.
    @return The name string in the active UI language.
    """
    name = tool["name"]
    return name if isinstance(name, str) else name[i18n.get_language()]


def print_catalog(use_color=True):
    """!
    @brief Print the full tool catalog to the terminal, grouped the same
           way the web-dashboard version is, in the active UI language.
    @param use_color If `False`, print without ANSI color codes (for
           `--plain`/log files/CI, same convention as `doctor.run_doctor()`).
    @return `0` (always succeeds -- there's nothing here that can fail).
    """
    lang = i18n.get_language()
    c = C if use_color else _NoColor
    total = sum(len(group["tools"]) for group in CATALOG)

    print(f"{c.BOLD}{t('catalog.header')}{c.RESET}\n")
    for group in CATALOG:
        print(f"{c.CYAN}{c.BOLD}{group['title'][lang]}{c.RESET}")
        print(f"{c.DIM}{tui.wrap(group['desc'][lang])}{c.RESET}\n")
        for tool in group["tools"]:
            tag_str = ""
            if tool["tags"]:
                tag_str = "  " + " ".join(f"{c.YELLOW}[{t(tag)}]{c.RESET}" for tag in tool["tags"])
            print(f"  {c.GREEN}{_tool_name(tool)}{c.RESET}{tag_str}")
            wrapped = tui.wrap(tool["desc"][lang], width=min(tui.term_width(), 78) - 4)
            for line in wrapped.splitlines():
                print(f"    {line}")
            print()
    print(f"{c.DIM}{t('catalog.footer', count=total)}{c.RESET}")
    return 0


class _NoColor:
    """!
    @brief Drop-in stand-in for `tui.C` with every attribute an empty
           string, so `print_catalog(use_color=False)` reuses the exact
           same formatting code with colors compiled out.
    """
    BOLD = DIM = CYAN = GREEN = YELLOW = RED = RESET = ""
