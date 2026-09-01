"""!
@file error_codes.py
@brief Human-readable lookup for Sony's SCE error codes: `psvita-toolkit errcode <query>`.

@details
Backed by a bundled plain-text DB (`data/vita_error_codes.txt`, 1419 lines of
`<C-code> <0xhex> <SCE_SYMBOL_NAME>`) copied from a reference dump used across
this toolkit's ports. That DB only has the raw symbol name -- no human
description -- so this module builds one two ways:

  1. `CURATED` -- hand-written, fuller explanations (what it means + why it
     usually happens on a Vita port + how to fix it) for the ~20 codes a
     porter is actually likely to hit (storage/memory/mount/savedata/network).
  2. A generic humanizer (`_humanize()`) for the other ~1400 -- splits the
     symbol into a subsystem prefix (`SUBSYSTEM_NAMES`) and a reason suffix
     (checked against the standard POSIX `errno` names first, then
     `GENERIC_REASON_TOKENS`), and composes a readable sentence from both.
     Never returns nothing: an unrecognized token still gets prettified
     (`NOT_A_KNOWN_TOKEN` -> "not a known token") rather than left raw.

Unlike every other user-facing string in this toolkit, the explanation text
is NOT gated behind the active `i18n` language: `explain()` always returns
all three languages at once, because the whole point of this lookup is a
porter pasting a code seen on a device screen (in whatever language the
device is in) and getting an answer usable regardless. Only the surrounding
menu chrome (`errcode.*` keys) goes through `t()` normally.
"""

import re
from pathlib import Path

from . import i18n
from . import tui
from .i18n import t
from .tui import C

STRINGS = {
    "errcode.menu_label": {
        "es": "Explicar un código de error de PS Vita",
        "en": "Explain a PS Vita error code",
        "pt": "Explicar um código de erro do PS Vita",
    },
    "errcode.title": {
        "es": "Explicar código de error",
        "en": "Explain error code",
        "pt": "Explicar código de erro",
    },
    "errcode.prompt": {
        "es": "Código (ej. 'C1-2764-9', '0x8001001c' o 'ENOSPC') -- Enter en blanco para volver:",
        "en": "Code (e.g. 'C1-2764-9', '0x8001001c' or 'ENOSPC') -- blank Enter to go back:",
        "pt": "Código (ex. 'C1-2764-9', '0x8001001c' ou 'ENOSPC') -- Enter em branco para voltar:",
    },
    "errcode.not_found": {
        "es": "No se encontró ningún código que coincida con '{query}' en la base de {total} códigos conocidos.",
        "en": "No code matching '{query}' was found in the database of {total} known codes.",
        "pt": "Nenhum código correspondente a '{query}' foi encontrado na base de {total} códigos conhecidos.",
    },
    "errcode.too_many_matches": {
        "es": "'{query}' coincide con {count} códigos -- sé más específico (probá con el código completo o el 0x...):",
        "en": "'{query}' matches {count} codes -- be more specific (try the full code or the 0x... value):",
        "pt": "'{query}' corresponde a {count} códigos -- seja mais específico (tente o código completo ou o 0x...):",
    },
    "errcode.result_header": {
        "es": "{ccode}   {hexcode}   {symbol}",
        "en": "{ccode}   {hexcode}   {symbol}",
        "pt": "{ccode}   {hexcode}   {symbol}",
    },
    "errcode.curated_tag": {
        "es": "(explicación detallada)",
        "en": "(detailed explanation)",
        "pt": "(explicação detalhada)",
    },
    "errcode.generic_tag": {
        "es": "(explicación generada del nombre del símbolo -- no hay ficha detallada para este código todavía)",
        "en": "(explanation generated from the symbol name -- no detailed writeup for this code yet)",
        "pt": "(explicação gerada a partir do nome do símbolo -- ainda não há ficha detalhada para este código)",
    },
}
i18n.register(STRINGS)

DB_PATH = Path(__file__).parent / "data" / "vita_error_codes.txt"

_ENTRIES = None  # lazy-loaded list of (ccode, hexcode, symbol), all upper/lower-normalized


def _load_db():
    """!
    @brief Parse the bundled error-code DB once and cache it in memory.
    @return list of `(ccode, hexcode, symbol)` tuples -- `ccode`/`symbol` upper-cased,
            `hexcode` lower-cased with a `0x` prefix, exactly as read from the file.
    """
    global _ENTRIES
    if _ENTRIES is not None:
        return _ENTRIES
    entries = []
    if DB_PATH.is_file():
        for line in DB_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if len(parts) != 3:
                continue
            ccode, hexcode, symbol = parts
            entries.append((ccode.upper(), hexcode.lower(), symbol.upper()))
    _ENTRIES = entries
    return entries


def _normalize_hex_query(q):
    """!
    @brief Turn a user-typed hex value into the `0x........` form the DB uses.
    @param q Raw query, already upper-cased by the caller.
    @return Normalized `"0x........"` string, or `""` if `q` isn't hex-shaped.
    """
    ql = q.lower()
    if ql.startswith("0x"):
        ql = ql[2:]
    if re.fullmatch(r"[0-9a-f]{6,8}", ql):
        return "0x" + ql
    return ""


def find_entries(query):
    """!
    @brief Look up a user-typed query against the error-code DB.
    @param query Raw text the user typed: a `C#-####-#` style code, a hex
           value (with or without `0x`, with or without leading zeros), a
           full `SCE_..._SYMBOL` name, or a bare fragment like `"ENOSPC"`.
    @return list of matching `(ccode, hexcode, symbol)` tuples. Tries, in
            order: exact C-code match, exact hex match, exact symbol match
            (with/without the `SCE_` prefix), then falls back to a substring
            search over every symbol name -- so a bare `"ENOSPC"` returns
            every code that ends in it (both `SCE_ERROR_ERRNO_ENOSPC` and
            `SCE_NET_ERROR_ENOSPC`) for the caller to disambiguate.
    """
    q = query.strip()
    if not q:
        return []
    entries = _load_db()
    qu = q.upper()

    exact_ccode = [e for e in entries if e[0] == qu]
    if exact_ccode:
        return exact_ccode

    qhex = _normalize_hex_query(qu)
    if qhex:
        exact_hex = [e for e in entries if e[1] == qhex]
        if exact_hex:
            return exact_hex

    qsym = qu if qu.startswith("SCE_") else f"SCE_{qu}"
    exact_symbol = [e for e in entries if e[2] == qu or e[2] == qsym]
    if exact_symbol:
        return exact_symbol

    return [e for e in entries if qu in e[2]]


# ---------------------------------------------------------------------------
# Curated explanations: the handful of codes worth a full write-up.
# ---------------------------------------------------------------------------

CURATED = {
    "SCE_ERROR_ERRNO_ENOSPC": {
        "es": "No queda espacio libre en el dispositivo de almacenamiento (memoria interna o tarjeta) donde se intentó escribir. Es el error más común al instalar un VPK o al guardar partida cuando la consola está casi llena. Solución: liberar espacio (borrar apps/capturas/dumps viejos, vaciar 'ux0:temp'), o instalar en una partición con más espacio libre si usás tarjeta SD.",
        "en": "There's no free space left on the storage device (internal memory or memory card) being written to. This is the most common error when installing a VPK or saving a game with the console nearly full. Fix: free up space (remove old apps/screenshots/dumps, empty 'ux0:temp'), or install to a partition with more room if you're using an SD card.",
        "pt": "Não há espaço livre no dispositivo de armazenamento (memória interna ou cartão) em que se tentou escrever. É o erro mais comum ao instalar um VPK ou salvar o jogo com o console quase cheio. Solução: liberar espaço (apagar apps/capturas/dumps antigos, esvaziar 'ux0:temp'), ou instalar em uma partição com mais espaço se estiver usando um cartão SD.",
    },
    "SCE_NET_ERROR_ENOSPC": {
        "es": "Sin espacio libre, pero reportado por la pila de red (sockets) en vez del sistema de archivos general -- normalmente aparece al escribir un buffer de recepción/envío muy grande, o si el almacenamiento usado para caché de red está lleno. Solución: mismo enfoque que el ENOSPC genérico -- liberar espacio en el dispositivo.",
        "en": "Out of free space, but reported by the networking stack (sockets) rather than the general filesystem -- usually shows up writing an oversized send/receive buffer, or when storage used for network caching is full. Fix: same approach as generic ENOSPC -- free up space on the device.",
        "pt": "Sem espaço livre, mas reportado pela pilha de rede (sockets) em vez do sistema de arquivos geral -- normalmente aparece ao escrever um buffer de envio/recebimento muito grande, ou quando o armazenamento usado para cache de rede está cheio. Solução: mesma abordagem do ENOSPC genérico -- liberar espaço no dispositivo.",
    },
    "SCE_ERROR_ERRNO_ENOMEM": {
        "es": "Sin memoria disponible para completar la operación (a nivel del sistema, no de tu app). En un port suele salir por fragmentación de memoria tras muchas cargas/descargas de assets, o porque el heap del sistema (no el tuyo) está agotado. Revisar liberación de recursos (texturas, buffers de archivo) que quedan sin cerrar.",
        "en": "No memory available to complete the operation (at the system level, not your app's own heap). On a port this usually shows up from memory fragmentation after many asset load/unload cycles, or because the system heap (not yours) is exhausted. Check for resources (textures, file buffers) that never get released.",
        "pt": "Sem memória disponível para concluir a operação (em nível de sistema, não do heap do seu app). Em um port, geralmente aparece por fragmentação de memória após muitos ciclos de carregar/descarregar assets, ou porque o heap do sistema (não o seu) está esgotado. Verifique recursos (texturas, buffers de arquivo) que nunca são liberados.",
    },
    "SCE_KERNEL_ERROR_NO_MEMORY": {
        "es": "El kernel no pudo reservar la memoria pedida (por ejemplo, al crear un thread, mutex, o mapear memoria con sceKernelAllocMemBlock). Muy común en ports con múltiples threads: cada uno reserva su propio stack, y sumados pueden agotar el heap disponible. Revisar tamaños de stack de threads y cuántos se crean.",
        "en": "The kernel couldn't allocate the requested memory (e.g. creating a thread, a mutex, or mapping memory via sceKernelAllocMemBlock). Very common in ports with several threads: each reserves its own stack, and combined they can exhaust the available heap. Check thread stack sizes and how many threads get spawned.",
        "pt": "O kernel não conseguiu alocar a memória solicitada (por exemplo, ao criar uma thread, um mutex, ou mapear memória via sceKernelAllocMemBlock). Muito comum em ports com várias threads: cada uma reserva sua própria stack, e somadas podem esgotar o heap disponível. Verifique o tamanho das stacks das threads e quantas são criadas.",
    },
    "SCE_APPUTIL_ERROR_NO_MEMORY": {
        "es": "sceAppUtil (savedata, mensajes de sistema, capturas de pantalla, etc.) no pudo reservar la memoria que necesita internamente para la operación. Suele pasar si se llama a una función de sceAppUtil justo cuando la app ya está usando casi toda la memoria disponible.",
        "en": "sceAppUtil (savedata, system messages, screenshots, etc.) couldn't allocate the memory it needs internally for the operation. Usually happens when an sceAppUtil call is made right when the app is already using nearly all available memory.",
        "pt": "sceAppUtil (savedata, mensagens do sistema, capturas de tela, etc.) não conseguiu alocar a memória que precisa internamente para a operação. Costuma acontecer quando uma chamada a sceAppUtil é feita bem quando o app já está usando quase toda a memória disponível.",
    },
    "SCE_ERROR_ERRNO_EACCES": {
        "es": "Permiso denegado -- se intentó una operación de archivo (leer/escribir/borrar) sobre una ruta a la que el proceso no tiene acceso, o sobre un archivo marcado de solo lectura. En un port, revisar que se esté escribiendo dentro de 'ux0:data/<titleid>/' y no en rutas del sistema.",
        "en": "Permission denied -- a file operation (read/write/delete) was attempted on a path the process doesn't have access to, or on a read-only-marked file. On a port, check that writes are happening inside 'ux0:data/<titleid>/' and not on system paths.",
        "pt": "Permissão negada -- foi tentada uma operação de arquivo (ler/escrever/apagar) em um caminho ao qual o processo não tem acesso, ou em um arquivo marcado como somente leitura. Em um port, verifique se as gravações acontecem dentro de 'ux0:data/<titleid>/' e não em caminhos do sistema.",
    },
    "SCE_ERROR_ERRNO_ENOENT": {
        "es": "No existe el archivo o directorio pedido. En un port casi siempre es una ruta de asset mal armada (mayúsculas/minúsculas, separador de carpetas, o un archivo que no se copió al VPK/tarjeta). Confirmar la ruta exacta con VitaShell.",
        "en": "The requested file or directory doesn't exist. On a port this is almost always a badly-built asset path (case sensitivity, folder separator, or a file that didn't get copied into the VPK/card). Verify the exact path with VitaShell.",
        "pt": "O arquivo ou diretório solicitado não existe. Em um port, quase sempre é um caminho de asset mal montado (maiúsculas/minúsculas, separador de pasta, ou um arquivo que não foi copiado para o VPK/cartão). Confirme o caminho exato com o VitaShell.",
    },
    "SCE_ERROR_ERRNO_EEXIST": {
        "es": "Ya existe un archivo/directorio con ese nombre donde se intentó crear uno nuevo (por ejemplo, un slot de guardado o una carpeta de config). Revisar si el código debería primero comprobar existencia (o sobreescribir) antes de crear.",
        "en": "A file/directory with that name already exists where a new one was being created (e.g. a save slot or a config folder). Check whether the code should check for existence first (or overwrite) before creating.",
        "pt": "Já existe um arquivo/diretório com esse nome onde se tentou criar um novo (por exemplo, um slot de save ou uma pasta de configuração). Verifique se o código deveria checar a existência (ou sobrescrever) antes de criar.",
    },
    "SCE_ERROR_ERRNO_EBUSY": {
        "es": "El recurso (archivo, dispositivo de almacenamiento, mutex) está en uso por otra operación y no se pudo completar la acción ahora. Suele resolverse esperando/reintentando, o revisando que no haya un handle de archivo sin cerrar de una operación anterior.",
        "en": "The resource (file, storage device, mutex) is in use by another operation and the action couldn't complete right now. Usually fixed by waiting/retrying, or checking for a file handle left open from a previous operation.",
        "pt": "O recurso (arquivo, dispositivo de armazenamento, mutex) está em uso por outra operação e a ação não pôde ser concluída agora. Geralmente se resolve esperando/tentando de novo, ou verificando se não há um handle de arquivo aberto de uma operação anterior.",
    },
    "SCE_ERROR_ERRNO_EROFS": {
        "es": "Se intentó escribir en un sistema de archivos montado de solo lectura (por ejemplo, 'app0:', donde vive el propio VPK instalado). Los datos de guardado/config deben escribirse en 'ux0:data/<titleid>/', nunca en 'app0:'.",
        "en": "A write was attempted on a filesystem mounted read-only (e.g. 'app0:', where the installed VPK itself lives). Savedata/config must be written to 'ux0:data/<titleid>/', never to 'app0:'.",
        "pt": "Foi feita uma tentativa de escrita em um sistema de arquivos montado como somente leitura (por exemplo, 'app0:', onde fica o próprio VPK instalado). Dados de save/config devem ser escritos em 'ux0:data/<titleid>/', nunca em 'app0:'.",
    },
    "SCE_APPUTIL_ERROR_NOT_INITIALIZED": {
        "es": "Se llamó a una función de sceAppUtil (savedata, parámetros de app, etc.) antes de inicializar el módulo con sceAppUtilInit(). Revisar el orden de inicialización del boilerplate del port.",
        "en": "An sceAppUtil function (savedata, app params, etc.) was called before the module was initialized with sceAppUtilInit(). Check the boilerplate's initialization order.",
        "pt": "Uma função de sceAppUtil (savedata, parâmetros do app, etc.) foi chamada antes de o módulo ser inicializado com sceAppUtilInit(). Verifique a ordem de inicialização do boilerplate do port.",
    },
    "SCE_APPUTIL_ERROR_NOT_MOUNTED": {
        "es": "No se pudo montar el sistema de guardado (savedata) -- suele pasar si la tarjeta de memoria no está insertada/reconocida, o si el titleid configurado no coincide con el del VPK instalado.",
        "en": "The savedata filesystem couldn't be mounted -- usually happens if the memory card isn't inserted/recognized, or if the configured titleid doesn't match the installed VPK's.",
        "pt": "Não foi possível montar o sistema de savedata -- geralmente acontece se o cartão de memória não estiver inserido/reconhecido, ou se o titleid configurado não corresponder ao do VPK instalado.",
    },
    "SCE_APPUTIL_ERROR_SAVEDATA_SLOT_EXISTS": {
        "es": "Se intentó crear un slot de guardado que ya existe. Si el port maneja slots de guardado manualmente, hay que comprobar existencia antes de crear, o usar el flag de sobreescritura.",
        "en": "An attempt was made to create a savedata slot that already exists. If the port manages savedata slots manually, check for existence before creating, or use the overwrite flag.",
        "pt": "Foi feita uma tentativa de criar um slot de savedata que já existe. Se o port gerencia slots de savedata manualmente, é preciso checar a existência antes de criar, ou usar a flag de sobrescrita.",
    },
    "SCE_APPUTIL_ERROR_SAVEDATA_SLOT_NOT_FOUND": {
        "es": "Se pidió leer/borrar un slot de guardado que no existe (por ejemplo, primer arranque sin partida guardada todavía). El código debería tratar esto como 'no hay guardado' en vez de un error fatal.",
        "en": "A read/delete was requested on a savedata slot that doesn't exist (e.g. first launch, no save yet). The code should treat this as 'no save exists' rather than a fatal error.",
        "pt": "Foi solicitada uma leitura/exclusão de um slot de savedata que não existe (por exemplo, primeira execução, ainda sem save). O código deveria tratar isso como 'não há save' em vez de um erro fatal.",
    },
    "SCE_KERNEL_ERROR_INVALID_ARGUMENT": {
        "es": "Se pasó un argumento inválido a una función del kernel (puntero nulo donde no se espera, tamaño/alineación incorrectos, un ID de handle que ya no existe). Suele ser un bug de la capa de wrapping JNI->nativo del port, no del motor original.",
        "en": "An invalid argument was passed to a kernel function (a null pointer where one isn't expected, wrong size/alignment, a handle ID that no longer exists). Usually a bug in the port's JNI->native wrapping layer, not the original engine.",
        "pt": "Foi passado um argumento inválido para uma função do kernel (um ponteiro nulo onde não se espera, tamanho/alinhamento incorretos, um ID de handle que não existe mais). Geralmente é um bug na camada de wrapping JNI->nativo do port, não do motor original.",
    },
    "SCE_ERROR_ERRNO_ETIMEDOUT": {
        "es": "La operación (típicamente de red o de espera de un recurso) tardó más del límite y se abortó. Si es red, revisar conectividad/Wi-Fi; si es un mutex/semáforo, puede indicar un deadlock disfrazado.",
        "en": "The operation (typically networking, or waiting on a resource) took longer than its limit and was aborted. If networking, check connectivity/Wi-Fi; if a mutex/semaphore, it can indicate a disguised deadlock.",
        "pt": "A operação (normalmente de rede, ou de espera por um recurso) demorou mais que o limite e foi abortada. Se for rede, verifique a conectividade/Wi-Fi; se for um mutex/semáforo, pode indicar um deadlock disfarçado.",
    },
    "SCE_ERROR_ERRNO_ECONNREFUSED": {
        "es": "El servidor remoto rechazó la conexión (nada escuchando en ese puerto, o firewall). Si el port apunta a servidores online del juego original que ya no existen, hay que neutralizar esas llamadas (ver so-patch en el toolkit).",
        "en": "The remote server refused the connection (nothing listening on that port, or a firewall). If the port is calling the original game's online servers and they no longer exist, those calls need to be neutralized (see the toolkit's so-patch).",
        "pt": "O servidor remoto recusou a conexão (nada escutando naquela porta, ou um firewall). Se o port está chamando os servidores online do jogo original e eles não existem mais, essas chamadas precisam ser neutralizadas (veja o so-patch do toolkit).",
    },
    "SCE_NET_CTL_ERROR_NOT_INITIALIZED": {
        "es": "Se llamó a una función de sceNetCtl (estado de Wi-Fi, IP, etc.) antes de inicializar sceNetCtlInit()/sceNet. Revisar orden de inicialización de red en el boilerplate.",
        "en": "An sceNetCtl function (Wi-Fi status, IP, etc.) was called before sceNetCtlInit()/sceNet was initialized. Check the boilerplate's networking init order.",
        "pt": "Uma função de sceNetCtl (status do Wi-Fi, IP, etc.) foi chamada antes de sceNetCtlInit()/sceNet ser inicializado. Verifique a ordem de inicialização de rede no boilerplate.",
    },
    "SCE_GXM_ERROR_INVALID_VALUE": {
        "es": "Un valor pasado a la API gráfica SceGxm es inválido (un enum fuera de rango, un puntero de memoria gráfica no alineado, un tamaño de buffer incorrecto). Muy común al portar el pipeline de render fijo (glVertexPointer/glTexParameterx) a GXM real -- revisar la capa vitaGL/traducción de estados.",
        "en": "A value passed to the SceGxm graphics API is invalid (an out-of-range enum, an unaligned graphics-memory pointer, a wrong buffer size). Very common when porting the fixed-function render pipeline (glVertexPointer/glTexParameterx) onto real GXM -- check the vitaGL/state-translation layer.",
        "pt": "Um valor passado à API gráfica SceGxm é inválido (um enum fora do intervalo, um ponteiro de memória gráfica desalinhado, um tamanho de buffer incorreto). Muito comum ao portar o pipeline de renderização fixo (glVertexPointer/glTexParameterx) para o GXM real -- verifique a camada vitaGL/tradução de estados.",
    },
}


# ---------------------------------------------------------------------------
# Generic humanizer: subsystem prefix + reason suffix, for every other code.
# ---------------------------------------------------------------------------

SUBSYSTEM_NAMES = {
    ("KERNEL",): {"es": "núcleo del sistema (SceKernel)", "en": "system kernel (SceKernel)", "pt": "núcleo do sistema (SceKernel)"},
    ("APPUTIL",): {"es": "utilidades de aplicación (guardado/config)", "en": "application utilities (savedata/config)", "pt": "utilitários de aplicativo (savedata/config)"},
    ("NET", "CTL"): {"es": "control de red (Wi-Fi/estado de conexión)", "en": "network control (Wi-Fi/connection state)", "pt": "controle de rede (Wi-Fi/estado da conexão)"},
    ("NET",): {"es": "red (sockets/TCP-IP)", "en": "networking (sockets/TCP-IP)", "pt": "rede (sockets/TCP-IP)"},
    ("NP", "MATCHING2"): {"es": "PlayStation Network -- matchmaking", "en": "PlayStation Network -- matchmaking", "pt": "PlayStation Network -- matchmaking"},
    ("NP", "COMMUNITY"): {"es": "PlayStation Network -- comunidad (rankings/mensajes)", "en": "PlayStation Network -- community (rankings/messaging)", "pt": "PlayStation Network -- comunidade (rankings/mensagens)"},
    ("NP", "AUTH"): {"es": "PlayStation Network -- autenticación", "en": "PlayStation Network -- authentication", "pt": "PlayStation Network -- autenticação"},
    ("NP", "UTIL"): {"es": "PlayStation Network -- utilidades", "en": "PlayStation Network -- utilities", "pt": "PlayStation Network -- utilitários"},
    ("NP", "BASIC"): {"es": "PlayStation Network -- funciones básicas (mensajes/amigos)", "en": "PlayStation Network -- basic features (messaging/friends)", "pt": "PlayStation Network -- funções básicas (mensagens/amigos)"},
    ("NP", "MANAGER"): {"es": "PlayStation Network -- gestor de sesión", "en": "PlayStation Network -- session manager", "pt": "PlayStation Network -- gerenciador de sessão"},
    ("NP",): {"es": "PlayStation Network", "en": "PlayStation Network", "pt": "PlayStation Network"},
    ("JPEG",): {"es": "decodificador JPEG", "en": "JPEG decoder", "pt": "decodificador JPEG"},
    ("XML",): {"es": "parser XML", "en": "XML parser", "pt": "parser XML"},
    ("HTTP",): {"es": "cliente HTTP", "en": "HTTP client", "pt": "cliente HTTP"},
    ("FIOS",): {"es": "sistema de archivos FIOS2", "en": "FIOS2 filesystem", "pt": "sistema de arquivos FIOS2"},
    ("GXM",): {"es": "gráficos (SceGxm/GPU)", "en": "graphics (SceGxm/GPU)", "pt": "gráficos (SceGxm/GPU)"},
    ("SNDP",): {"es": "reproductor de sonido de sistema", "en": "system sound player", "pt": "reprodutor de som do sistema"},
    ("SAS",): {"es": "mezclador de audio (SceSas)", "en": "audio mixer (SceSas)", "pt": "mixer de áudio (SceSas)"},
    ("VOICE",): {"es": "chat de voz", "en": "voice chat", "pt": "chat de voz"},
    ("SULPHA",): {"es": "depurador de gráficos (Sulpha)", "en": "graphics debugger (Sulpha)", "pt": "depurador de gráficos (Sulpha)"},
    ("FONT",): {"es": "renderizado de fuentes", "en": "font rendering", "pt": "renderização de fontes"},
    ("CAMERA",): {"es": "cámara", "en": "camera", "pt": "câmera"},
    ("AUDIOENC",): {"es": "codificador de audio", "en": "audio encoder", "pt": "codificador de áudio"},
    ("AUDIODEC",): {"es": "decodificador de audio", "en": "audio decoder", "pt": "decodificador de áudio"},
    ("VIDEODEC",): {"es": "decodificador de video", "en": "video decoder", "pt": "decodificador de vídeo"},
    ("AVCDEC",): {"es": "decodificador de video AVC/H.264", "en": "AVC/H.264 video decoder", "pt": "decodificador de vídeo AVC/H.264"},
    ("RTC",): {"es": "reloj de tiempo real (fecha/hora)", "en": "real-time clock (date/time)", "pt": "relógio de tempo real (data/hora)"},
    ("COMMON", "DIALOG"): {"es": "diálogos del sistema (teclado, mensajes, etc.)", "en": "system dialogs (keyboard, messages, etc.)", "pt": "diálogos do sistema (teclado, mensagens, etc.)"},
    ("CES",): {"es": "conversión de codificación de texto", "en": "character-encoding conversion", "pt": "conversão de codificação de texto"},
    ("AUDIO", "OUT"): {"es": "salida de audio", "en": "audio output", "pt": "saída de áudio"},
    ("AUDIO", "IN"): {"es": "entrada de audio (micrófono)", "en": "audio input (microphone)", "pt": "entrada de áudio (microfone)"},
    ("PHOTO", "EXPORT"): {"es": "exportación de fotos", "en": "photo export", "pt": "exportação de fotos"},
    ("ULT",): {"es": "hilos ligeros (SceUlt)", "en": "lightweight threads (SceUlt)", "pt": "threads leves (SceUlt)"},
    ("NGS",): {"es": "sistema de audio de nueva generación (SceNgs)", "en": "next-gen audio system (SceNgs)", "pt": "sistema de áudio de nova geração (SceNgs)"},
    ("MOTION",): {"es": "sensores de movimiento", "en": "motion sensors", "pt": "sensores de movimento"},
    ("FIBER",): {"es": "fibers (corutinas de bajo nivel)", "en": "fibers (low-level coroutines)", "pt": "fibers (corrotinas de baixo nível)"},
    ("DISPLAY",): {"es": "salida de video/pantalla", "en": "video/display output", "pt": "saída de vídeo/tela"},
    ("PHOTOREVIEW", "DIALOG"): {"es": "diálogo de revisión de fotos", "en": "photo review dialog", "pt": "diálogo de revisão de fotos"},
    ("IME",): {"es": "editor de método de entrada (teclado en pantalla)", "en": "input method editor (on-screen keyboard)", "pt": "editor de método de entrada (teclado na tela)"},
    ("SCREENSHOT",): {"es": "capturas de pantalla", "en": "screenshots", "pt": "capturas de tela"},
    ("SYSMODULE",): {"es": "carga de módulos del sistema", "en": "system module loading", "pt": "carregamento de módulos do sistema"},
}

# Standard POSIX errno short names -> human meaning. Covers SCE_ERROR_ERRNO_*
# and the many SCE_NET_ERROR_* / SCE_*_ERROR_* codes that just mirror errno.
ERRNO_MEANINGS = {
    "EPERM": {"es": "operación no permitida", "en": "operation not permitted", "pt": "operação não permitida"},
    "ENOENT": {"es": "el archivo o directorio no existe", "en": "no such file or directory", "pt": "arquivo ou diretório inexistente"},
    "ESRCH": {"es": "no existe ese proceso", "en": "no such process", "pt": "processo inexistente"},
    "EINTR": {"es": "la llamada fue interrumpida", "en": "call was interrupted", "pt": "chamada foi interrompida"},
    "EIO": {"es": "error de entrada/salida de hardware", "en": "hardware input/output error", "pt": "erro de entrada/saída de hardware"},
    "ENXIO": {"es": "no existe ese dispositivo/dirección", "en": "no such device or address", "pt": "dispositivo ou endereço inexistente"},
    "E2BIG": {"es": "lista de argumentos demasiado larga", "en": "argument list too long", "pt": "lista de argumentos muito longa"},
    "ENOEXEC": {"es": "formato de ejecutable inválido", "en": "invalid executable format", "pt": "formato de executável inválido"},
    "EBADF": {"es": "descriptor de archivo inválido", "en": "bad file descriptor", "pt": "descritor de arquivo inválido"},
    "ECHILD": {"es": "no existe ese proceso hijo", "en": "no child process", "pt": "processo filho inexistente"},
    "EAGAIN": {"es": "el recurso no está disponible en este momento, reintentar", "en": "resource temporarily unavailable, try again", "pt": "recurso temporariamente indisponível, tente novamente"},
    "EWOULDBLOCK": {"es": "la operación bloquearía; se pidió modo no-bloqueante", "en": "operation would block; non-blocking mode was requested", "pt": "a operação bloquearia; modo não bloqueante foi solicitado"},
    "ENOMEM": {"es": "sin memoria disponible", "en": "not enough memory available", "pt": "sem memória disponível"},
    "EACCES": {"es": "permiso denegado", "en": "permission denied", "pt": "permissão negada"},
    "EFAULT": {"es": "dirección de memoria inválida", "en": "bad memory address", "pt": "endereço de memória inválido"},
    "ENOTBLK": {"es": "no es un dispositivo de bloques", "en": "not a block device", "pt": "não é um dispositivo de blocos"},
    "EBUSY": {"es": "el recurso está ocupado", "en": "resource is busy", "pt": "recurso está ocupado"},
    "EEXIST": {"es": "el archivo ya existe", "en": "file already exists", "pt": "arquivo já existe"},
    "EXDEV": {"es": "enlace entre dispositivos distintos", "en": "cross-device link", "pt": "link entre dispositivos diferentes"},
    "ENODEV": {"es": "no existe ese dispositivo", "en": "no such device", "pt": "dispositivo inexistente"},
    "ENOTDIR": {"es": "no es un directorio", "en": "not a directory", "pt": "não é um diretório"},
    "EISDIR": {"es": "es un directorio (se esperaba un archivo)", "en": "is a directory (a file was expected)", "pt": "é um diretório (esperava-se um arquivo)"},
    "EINVAL": {"es": "argumento inválido", "en": "invalid argument", "pt": "argumento inválido"},
    "ENFILE": {"es": "límite de archivos abiertos del sistema alcanzado", "en": "system-wide open-file limit reached", "pt": "limite de arquivos abertos do sistema atingido"},
    "EMFILE": {"es": "límite de archivos abiertos del proceso alcanzado", "en": "per-process open-file limit reached", "pt": "limite de arquivos abertos do processo atingido"},
    "ENOTTY": {"es": "operación de control inapropiada para el dispositivo", "en": "inappropriate ioctl for device", "pt": "operação de controle inadequada para o dispositivo"},
    "EFBIG": {"es": "el archivo es demasiado grande", "en": "file too large", "pt": "arquivo grande demais"},
    "ENOSPC": {"es": "no queda espacio libre en el dispositivo", "en": "no free space left on the device", "pt": "sem espaço livre no dispositivo"},
    "ESPIPE": {"es": "posicionamiento inválido (seek) sobre este descriptor", "en": "invalid seek on this descriptor", "pt": "posicionamento (seek) inválido neste descritor"},
    "EROFS": {"es": "sistema de archivos de solo lectura", "en": "read-only filesystem", "pt": "sistema de arquivos somente leitura"},
    "EMLINK": {"es": "demasiados enlaces (hard links)", "en": "too many links", "pt": "excesso de links (hard links)"},
    "EPIPE": {"es": "tubería (pipe) rota, nadie está leyendo del otro lado", "en": "broken pipe, nobody is reading on the other end", "pt": "pipe quebrado, ninguém está lendo do outro lado"},
    "EDOM": {"es": "argumento fuera del dominio matemático de la función", "en": "argument outside the function's mathematical domain", "pt": "argumento fora do domínio matemático da função"},
    "ERANGE": {"es": "resultado fuera de rango representable", "en": "result out of representable range", "pt": "resultado fora do intervalo representável"},
    "EDEADLK": {"es": "se detectó un deadlock (bloqueo mutuo)", "en": "deadlock detected", "pt": "deadlock (bloqueio mútuo) detectado"},
    "ENAMETOOLONG": {"es": "el nombre de archivo/ruta es demasiado largo", "en": "file name/path is too long", "pt": "nome de arquivo/caminho é longo demais"},
    "ENOLCK": {"es": "sin bloqueos (locks) disponibles", "en": "no locks available", "pt": "sem locks disponíveis"},
    "ENOSYS": {"es": "la función no está implementada", "en": "function not implemented", "pt": "função não implementada"},
    "ENOTEMPTY": {"es": "el directorio no está vacío", "en": "directory not empty", "pt": "diretório não está vazio"},
    "ELOOP": {"es": "demasiados enlaces simbólicos anidados", "en": "too many nested symbolic links", "pt": "excesso de links simbólicos aninhados"},
    "EOVERFLOW": {"es": "el valor no entra en el tipo de dato usado", "en": "value doesn't fit in the data type used", "pt": "o valor não cabe no tipo de dado usado"},
    "EILSEQ": {"es": "secuencia de bytes ilegal (codificación de texto)", "en": "illegal byte sequence (text encoding)", "pt": "sequência de bytes ilegal (codificação de texto)"},
    "ENOTSOCK": {"es": "el descriptor no es un socket", "en": "the descriptor is not a socket", "pt": "o descritor não é um socket"},
    "EDESTADDRREQ": {"es": "falta la dirección de destino", "en": "destination address required", "pt": "endereço de destino ausente"},
    "EMSGSIZE": {"es": "el mensaje es demasiado grande para el socket", "en": "message is too large for the socket", "pt": "mensagem grande demais para o socket"},
    "EPROTOTYPE": {"es": "tipo de protocolo incorrecto para el socket", "en": "wrong protocol type for the socket", "pt": "tipo de protocolo incorreto para o socket"},
    "ENOPROTOOPT": {"es": "opción de protocolo no disponible", "en": "protocol option not available", "pt": "opção de protocolo não disponível"},
    "EPROTONOSUPPORT": {"es": "protocolo no soportado", "en": "protocol not supported", "pt": "protocolo não suportado"},
    "ESOCKTNOSUPPORT": {"es": "tipo de socket no soportado", "en": "socket type not supported", "pt": "tipo de socket não suportado"},
    "EOPNOTSUPP": {"es": "operación no soportada en ese socket", "en": "operation not supported on that socket", "pt": "operação não suportada nesse socket"},
    "EPFNOSUPPORT": {"es": "familia de protocolos no soportada", "en": "protocol family not supported", "pt": "família de protocolos não suportada"},
    "EAFNOSUPPORT": {"es": "familia de direcciones no soportada", "en": "address family not supported", "pt": "família de endereços não suportada"},
    "EADDRINUSE": {"es": "la dirección/puerto ya está en uso", "en": "address/port already in use", "pt": "endereço/porta já em uso"},
    "EADDRNOTAVAIL": {"es": "la dirección solicitada no está disponible", "en": "requested address is not available", "pt": "o endereço solicitado não está disponível"},
    "ENETDOWN": {"es": "la red está caída", "en": "network is down", "pt": "a rede está inativa"},
    "ENETUNREACH": {"es": "la red no es alcanzable (sin ruta)", "en": "network is unreachable (no route)", "pt": "a rede está inacessível (sem rota)"},
    "ENETRESET": {"es": "la conexión fue reiniciada por la red", "en": "connection reset by the network", "pt": "a conexão foi reiniciada pela rede"},
    "ECONNABORTED": {"es": "la conexión fue abortada localmente", "en": "connection aborted locally", "pt": "a conexão foi abortada localmente"},
    "ECONNRESET": {"es": "la otra parte cerró/reinició la conexión abruptamente", "en": "the other end reset/closed the connection abruptly", "pt": "o outro lado reiniciou/fechou a conexão abruptamente"},
    "ENOBUFS": {"es": "sin espacio en los buffers de red", "en": "no space in network buffers", "pt": "sem espaço nos buffers de rede"},
    "EISCONN": {"es": "el socket ya está conectado", "en": "the socket is already connected", "pt": "o socket já está conectado"},
    "ENOTCONN": {"es": "el socket no está conectado", "en": "the socket is not connected", "pt": "o socket não está conectado"},
    "ESHUTDOWN": {"es": "no se puede enviar; el socket ya se cerró para escritura", "en": "can't send; the socket has already been shut down for writing", "pt": "não é possível enviar; o socket já foi encerrado para escrita"},
    "ETOOMANYREFS": {"es": "demasiadas referencias, no se pueden empalmar", "en": "too many references, can't splice", "pt": "referências demais, não é possível unir (splice)"},
    "ETIMEDOUT": {"es": "se agotó el tiempo de espera de la operación", "en": "the operation timed out", "pt": "o tempo limite da operação foi excedido"},
    "ECONNREFUSED": {"es": "el servidor remoto rechazó la conexión", "en": "the remote server refused the connection", "pt": "o servidor remoto recusou a conexão"},
    "EHOSTDOWN": {"es": "el host remoto está caído", "en": "the remote host is down", "pt": "o host remoto está inativo"},
    "EHOSTUNREACH": {"es": "el host remoto no es alcanzable", "en": "the remote host is unreachable", "pt": "o host remoto está inacessível"},
    "EALREADY": {"es": "ya hay una operación en curso sobre este socket", "en": "an operation is already in progress on this socket", "pt": "já há uma operação em andamento nesse socket"},
    "EINPROGRESS": {"es": "la operación está en curso (no bloqueante)", "en": "the operation is now in progress (non-blocking)", "pt": "a operação está em andamento (não bloqueante)"},
    "ESTALE": {"es": "el handle de archivo remoto ya no es válido", "en": "the remote file handle is no longer valid", "pt": "o handle de arquivo remoto não é mais válido"},
    "EDQUOT": {"es": "se superó la cuota de disco asignada", "en": "disk quota exceeded", "pt": "cota de disco excedida"},
    "ECANCELED": {"es": "la operación fue cancelada", "en": "the operation was canceled", "pt": "a operação foi cancelada"},
    "EIDRM": {"es": "el identificador (semáforo/cola) fue eliminado", "en": "the identifier (semaphore/queue) was removed", "pt": "o identificador (semáforo/fila) foi removido"},
    "ENOMSG": {"es": "no hay un mensaje del tipo esperado", "en": "no message of the expected type", "pt": "não há mensagem do tipo esperado"},
    "ENODATA": {"es": "sin datos disponibles", "en": "no data available", "pt": "sem dados disponíveis"},
    "ETIME": {"es": "expiró el temporizador de la llamada", "en": "the call's timer expired", "pt": "o temporizador da chamada expirou"},
    "EBADMSG": {"es": "mensaje corrupto o con formato incorrecto", "en": "corrupt or badly-formatted message", "pt": "mensagem corrompida ou mal formatada"},
    "EMULTIHOP": {"es": "se requerirían múltiples saltos, no soportado", "en": "multihop attempted, not supported", "pt": "seriam necessários múltiplos saltos, não suportado"},
    "ENOLINK": {"es": "el enlace ya no está activo", "en": "the link is no longer active", "pt": "o link não está mais ativo"},
    "EPROTO": {"es": "error de protocolo", "en": "protocol error", "pt": "erro de protocolo"},
}

# Non-errno reason suffixes, longest key first when matched.
GENERIC_REASON_TOKENS = {
    "NOT_INITIALIZED": {"es": "el módulo no fue inicializado antes de usarlo", "en": "the module wasn't initialized before use", "pt": "o módulo não foi inicializado antes do uso"},
    "ALREADY_INITIALIZED": {"es": "el módulo ya estaba inicializado", "en": "the module was already initialized", "pt": "o módulo já estava inicializado"},
    "NOT_FOUND": {"es": "no se encontró lo que se pedía", "en": "what was requested wasn't found", "pt": "o que foi solicitado não foi encontrado"},
    "NOT_SUPPORTED": {"es": "operación no soportada en esta consola/versión", "en": "operation not supported on this console/version", "pt": "operação não suportada neste console/versão"},
    "UNSUPPORTED": {"es": "no soportado", "en": "unsupported", "pt": "não suportado"},
    "NOT_MOUNTED": {"es": "el volumen/dispositivo no está montado", "en": "the volume/device isn't mounted", "pt": "o volume/dispositivo não está montado"},
    "ALREADY_EXISTS": {"es": "ya existe", "en": "already exists", "pt": "já existe"},
    "BUSY": {"es": "el recurso está ocupado", "en": "the resource is busy", "pt": "o recurso está ocupado"},
    "TIMEOUT": {"es": "se agotó el tiempo de espera", "en": "the operation timed out", "pt": "o tempo limite foi excedido"},
    "INVALID_ARGUMENT": {"es": "se pasó un argumento inválido", "en": "an invalid argument was passed", "pt": "foi passado um argumento inválido"},
    "INVALID_VALUE": {"es": "se pasó un valor inválido", "en": "an invalid value was passed", "pt": "foi passado um valor inválido"},
    "INVALID_ID": {"es": "el identificador (handle) es inválido", "en": "the handle/ID is invalid", "pt": "o identificador (handle) é inválido"},
    "INVALID_SIZE": {"es": "el tamaño indicado es inválido", "en": "the given size is invalid", "pt": "o tamanho indicado é inválido"},
    "INVALID_POINTER": {"es": "el puntero pasado es inválido", "en": "the given pointer is invalid", "pt": "o ponteiro passado é inválido"},
    "INVALID_INDEX": {"es": "el índice indicado es inválido", "en": "the given index is invalid", "pt": "o índice indicado é inválido"},
    "INVALID_FORMAT": {"es": "el formato de los datos es inválido", "en": "the data format is invalid", "pt": "o formato dos dados é inválido"},
    "OUT_OF_MEMORY": {"es": "sin memoria disponible", "en": "out of available memory", "pt": "sem memória disponível"},
    "NO_MEMORY": {"es": "sin memoria disponible", "en": "no memory available", "pt": "sem memória disponível"},
    "MEMORY": {"es": "problema de memoria", "en": "memory-related problem", "pt": "problema relacionado à memória"},
    "FULL": {"es": "lleno, sin espacio para más elementos", "en": "full, no room for more entries", "pt": "cheio, sem espaço para mais itens"},
    "EMPTY": {"es": "vacío cuando se esperaba contenido", "en": "empty when content was expected", "pt": "vazio quando se esperava conteúdo"},
    "DENIED": {"es": "acceso denegado", "en": "access denied", "pt": "acesso negado"},
    "PERMISSION": {"es": "problema de permisos", "en": "permission-related problem", "pt": "problema de permissão"},
    "ABORTED": {"es": "la operación fue abortada", "en": "the operation was aborted", "pt": "a operação foi abortada"},
    "ABORT": {"es": "la operación fue abortada", "en": "the operation was aborted", "pt": "a operação foi abortada"},
    "CANCELLED": {"es": "la operación fue cancelada", "en": "the operation was canceled", "pt": "a operação foi cancelada"},
    "CANCELED": {"es": "la operación fue cancelada", "en": "the operation was canceled", "pt": "a operação foi cancelada"},
    "CANCEL": {"es": "la operación fue cancelada", "en": "the operation was canceled", "pt": "a operação foi cancelada"},
    "FATAL": {"es": "error fatal e irrecuperable", "en": "fatal, unrecoverable error", "pt": "erro fatal e irrecuperável"},
    "PARSE": {"es": "no se pudo interpretar (parsear) el contenido", "en": "the content couldn't be parsed", "pt": "não foi possível interpretar (parsear) o conteúdo"},
    "FORMAT": {"es": "problema de formato de los datos", "en": "data-format problem", "pt": "problema no formato dos dados"},
    "VERSION": {"es": "problema de versión (incompatible o desconocida)", "en": "version problem (incompatible or unknown)", "pt": "problema de versão (incompatível ou desconhecida)"},
    "PARAM": {"es": "parámetro inválido", "en": "invalid parameter", "pt": "parâmetro inválido"},
    "SIZE": {"es": "problema con el tamaño indicado", "en": "problem with the given size", "pt": "problema com o tamanho indicado"},
    "OVERFLOW": {"es": "desbordamiento (el valor no entra en el espacio reservado)", "en": "overflow (the value doesn't fit in the allotted space)", "pt": "estouro (o valor não cabe no espaço reservado)"},
    "DISCONNECTED": {"es": "la conexión se cortó", "en": "the connection was dropped", "pt": "a conexão foi interrompida"},
    "LOGIN": {"es": "problema de inicio de sesión", "en": "login problem", "pt": "problema de login"},
    "EXPIRED": {"es": "expiró (token/sesión/licencia vencida)", "en": "expired (token/session/license no longer valid)", "pt": "expirou (token/sessão/licença não é mais válida)"},
    "LOCKED": {"es": "el recurso está bloqueado", "en": "the resource is locked", "pt": "o recurso está bloqueado"},
    "NOT_EMPTY": {"es": "no está vacío cuando se esperaba que lo estuviera", "en": "not empty when it was expected to be", "pt": "não está vazio quando se esperava que estivesse"},
    "EXCEEDED": {"es": "se superó un límite del sistema", "en": "a system limit was exceeded", "pt": "um limite do sistema foi excedido"},
    "UNKNOWN": {"es": "causa desconocida/no documentada", "en": "unknown/undocumented cause", "pt": "causa desconhecida/não documentada"},
    "INTERNAL": {"es": "error interno del sistema", "en": "internal system error", "pt": "erro interno do sistema"},
    "DRIVER": {"es": "problema a nivel del driver de hardware", "en": "hardware-driver-level problem", "pt": "problema em nível de driver de hardware"},
    "STATE": {"es": "la operación no es válida en el estado actual", "en": "the operation isn't valid in the current state", "pt": "a operação não é válida no estado atual"},
    "NULL_POINTER": {"es": "se pasó un puntero nulo donde no se esperaba", "en": "a null pointer was passed where one wasn't expected", "pt": "foi passado um ponteiro nulo onde não se esperava"},
    "OUT_OF_RANGE": {"es": "el valor está fuera del rango permitido", "en": "the value is out of the allowed range", "pt": "o valor está fora do intervalo permitido"},
    "NOT_READY": {"es": "el recurso todavía no está listo", "en": "the resource isn't ready yet", "pt": "o recurso ainda não está pronto"},
    "RESOLVER": {"es": "resolución de DNS", "en": "DNS resolution", "pt": "resolução de DNS"},
}
_REASON_KEYS_BY_LENGTH = sorted(GENERIC_REASON_TOKENS, key=len, reverse=True)


def _prettify(tokens):
    """!
    @brief Turn `["NOT", "A", "KNOWN", "TOKEN"]` into `"not a known token"`.
    @param tokens List of symbol-name tokens (already split on `_`).
    @return Lowercased, space-joined string.
    """
    return " ".join(tokens).lower()


def _split_symbol(symbol):
    """!
    @brief Split an `SCE_..._SYMBOL` name into `(subsystem_tokens, reason_tokens)`.
    @param symbol Upper-case symbol name, e.g. `"SCE_APPUTIL_ERROR_NOT_INITIALIZED"`.
    @return `(subsystem_tokens, reason_tokens)` -- the first `ERROR` token (the
            convention's separator between subsystem and reason) is dropped;
            if no `ERROR` token exists, the first remaining token is treated
            as the subsystem and everything else as the reason.
    """
    tokens = symbol.split("_")
    if tokens and tokens[0] == "SCE":
        tokens = tokens[1:]
    if "ERROR" in tokens:
        idx = tokens.index("ERROR")
        subsystem = tokens[:idx] or ["GENERIC"]
        reason = tokens[idx + 1:]
    else:
        subsystem = tokens[:1] or ["GENERIC"]
        reason = tokens[1:]
    return subsystem, reason


def _lookup_subsystem_name(subsystem_tokens):
    """!
    @brief Resolve a subsystem-token tuple to its human name, trying longest
           prefix match first, then falling back to a prettified generic name.
    @param subsystem_tokens List of tokens, e.g. `["NET", "CTL"]` or `["APPUTIL"]`.
    @return `{"es": ..., "en": ..., "pt": ...}` dict.
    """
    for length in range(len(subsystem_tokens), 0, -1):
        key = tuple(subsystem_tokens[:length])
        if key in SUBSYSTEM_NAMES:
            return SUBSYSTEM_NAMES[key]
    pretty = _prettify(subsystem_tokens)
    if subsystem_tokens[:1] == ["NP"]:
        return {
            "es": f"PlayStation Network -- {pretty[3:].strip() or 'general'}",
            "en": f"PlayStation Network -- {pretty[3:].strip() or 'general'}",
            "pt": f"PlayStation Network -- {pretty[3:].strip() or 'geral'}",
        }
    return {
        "es": f"subsistema '{pretty}'",
        "en": f"'{pretty}' subsystem",
        "pt": f"subsistema '{pretty}'",
    }


def _lookup_reason(reason_tokens):
    """!
    @brief Resolve a reason-token list to a human phrase, trying (in order)
           a standard POSIX errno name, an exact generic-token match, a
           suffix match against known generic tokens, then a raw prettify.
    @param reason_tokens List of tokens, e.g. `["ERRNO", "ENOSPC"]` or `["NOT", "INITIALIZED"]`.
    @return `{"es": ..., "en": ..., "pt": ...}` dict.
    """
    if not reason_tokens:
        return {"es": "causa no especificada", "en": "unspecified cause", "pt": "causa não especificada"}

    if reason_tokens[-1] in ERRNO_MEANINGS:
        return ERRNO_MEANINGS[reason_tokens[-1]]

    reason_str = "_".join(reason_tokens)
    if reason_str in GENERIC_REASON_TOKENS:
        return GENERIC_REASON_TOKENS[reason_str]

    for key in _REASON_KEYS_BY_LENGTH:
        if reason_str.endswith(key):
            return GENERIC_REASON_TOKENS[key]

    pretty = _prettify(reason_tokens)
    return {
        "es": f"relacionado con '{pretty}'",
        "en": f"related to '{pretty}'",
        "pt": f"relacionado a '{pretty}'",
    }


def _humanize(symbol):
    """!
    @brief Generate a readable 3-language explanation from a bare symbol name.
    @param symbol Upper-case `SCE_..._SYMBOL` name not present in `CURATED`.
    @return `{"es": ..., "en": ..., "pt": ...}` sentence dict.
    """
    subsystem_tokens, reason_tokens = _split_symbol(symbol)
    subsystem = _lookup_subsystem_name(subsystem_tokens)
    reason = _lookup_reason(reason_tokens)
    en_subsystem = subsystem["en"]
    en_subsystem = en_subsystem[0].upper() + en_subsystem[1:]
    return {
        "es": f"Error de {subsystem['es']}: {reason['es']}.",
        "en": f"{en_subsystem} error: {reason['en']}.",
        "pt": f"Erro de {subsystem['pt']}: {reason['pt']}.",
    }


def explain(query):
    """!
    @brief Public entry point: resolve a user query into a full explanation.
    @param query Raw user-typed text (see `find_entries()` for accepted forms).
    @return dict with:
            - `"status"`: `"ok"`, `"not_found"`, or `"ambiguous"`.
            - `"matches"`: the list of `(ccode, hexcode, symbol)` tuples found
              (only set for `"ambiguous"`, capped at 15 for display).
            - `"entry"`: the single `(ccode, hexcode, symbol)` match (only for `"ok"`).
            - `"text"`: `{"es", "en", "pt"}` explanation dict (only for `"ok"`).
            - `"curated"`: `True` if `"text"` came from `CURATED` rather than
              the generic humanizer (only for `"ok"`).
    """
    matches = find_entries(query)
    if not matches:
        return {"status": "not_found"}
    if len(matches) > 1:
        return {"status": "ambiguous", "matches": matches[:15]}

    entry = matches[0]
    symbol = entry[2]
    if symbol in CURATED:
        return {"status": "ok", "entry": entry, "text": CURATED[symbol], "curated": True}
    return {"status": "ok", "entry": entry, "text": _humanize(symbol), "curated": False}


def _print_result(result, query):
    """!
    @brief Print one `explain()` result to the terminal, in the active menu
           language for chrome text but always all 3 languages for the explanation.
    @param result Return value of `explain()`.
    @param query The original user query (for the not-found message).
    """
    total = len(_load_db())
    if result["status"] == "not_found":
        print(f"\n{C.RED}{t('errcode.not_found', query=query, total=total)}{C.RESET}")
        return
    if result["status"] == "ambiguous":
        print(f"\n{C.YELLOW}{t('errcode.too_many_matches', query=query, count=len(result['matches']))}{C.RESET}")
        for ccode, hexcode, symbol in result["matches"]:
            print(f"  {ccode:<14}{hexcode:<14}{symbol}")
        return

    ccode, hexcode, symbol = result["entry"]
    tag = t("errcode.curated_tag") if result["curated"] else t("errcode.generic_tag")
    print(f"\n{C.BOLD}{C.CYAN}{t('errcode.result_header', ccode=ccode, hexcode=hexcode, symbol=symbol)}{C.RESET}")
    print(f"{C.DIM}{tag}{C.RESET}\n")
    for lang in i18n.SUPPORTED_LANGUAGES:
        print(f"{C.BOLD}[{lang.upper()}]{C.RESET} {result['text'][lang]}\n")


def run_lookup(query):
    """!
    @brief Headless entry point shared by the CLI subcommand.
    @param query Raw user-typed query.
    @return Process-style exit code: `0` on a single match, `1` otherwise
            (not found or ambiguous -- both need a human to look at the output).
    """
    result = explain(query)
    _print_result(result, query)
    return 0 if result["status"] == "ok" else 1


def menu_lookup(global_cfg):
    """!
    @brief TUI-facing loop: prompt for a code, print the explanation, repeat
           until the user submits a blank line.
    @param global_cfg Global config dict (unused; kept for the same call
           signature as every other main-menu item).
    """
    tui.clear()
    tui.print_banner(t("errcode.title"))
    while True:
        query = input(f"\n{C.BOLD}{t('errcode.prompt')}{C.RESET}\n> ").strip()
        if not query:
            break
        run_lookup(query)
