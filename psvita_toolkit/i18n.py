"""
Sistema simple de internacionalización (i18n) del toolkit -- sin dependencias
externas (no gettext/.po, un dict en memoria alcanza para el volumen de texto
de esta herramienta).

Cada módulo declara su propio diccionario de strings, namespaced por módulo
para que las claves nunca choquen entre archivos distintos:

    from . import i18n
    from .i18n import t

    STRINGS = {
        "init_port.game_name_prompt": {
            "es": "Nombre del juego (display, ej. 'Inotia 4'):",
            "en": "Game name (display, e.g. 'Inotia 4'):",
            "pt": "Nome do jogo (exibição, ex. 'Inotia 4'):",
        },
    }
    i18n.register(STRINGS)

    ...
    input(t("init_port.game_name_prompt"))

Los valores con variables usan la sintaxis de str.format():

    "init_port.summary_game": {"es": "  Juego:    {name}", ...}
    ...
    print(t("init_port.summary_game", name=game_name))

El idioma activo se fija UNA sola vez al arrancar -- config.py lo pregunta en
el primer uso (o lo carga de ~/.psvita-toolkit/config.json en las siguientes)
y llama a set_language() antes de mostrar cualquier otro menú, así que para
cuando el resto del toolkit corre, t() ya devuelve el idioma correcto.
"""

SUPPORTED_LANGUAGES = ("es", "en", "pt")
LANGUAGE_NAMES = {"es": "Español", "en": "English", "pt": "Português"}
DEFAULT_LANGUAGE = "es"

_lang = DEFAULT_LANGUAGE
_catalog = {}


def register(strings):
    """Cada módulo llama esto una vez, al importarse, con su propio dict de
    traducciones. Última en registrar gana si dos módulos usaran la misma
    clave por error -- por eso la convención es namespacear con el nombre
    del módulo como prefijo ('modulo.algo')."""
    _catalog.update(strings)


def set_language(code):
    global _lang
    if code in SUPPORTED_LANGUAGES:
        _lang = code


def get_language():
    return _lang


def t(key, **kwargs):
    """Traduce 'key' al idioma activo. Si la clave no está registrada,
    devuelve la clave tal cual (mejor un texto en inglés/clave visible que un
    KeyError que tumbe el menú). Si falta la traducción para el idioma activo
    específicamente, cae a español y después a la primera que haya."""
    entry = _catalog.get(key)
    if entry is None:
        return key
    text = entry.get(_lang) or entry.get(DEFAULT_LANGUAGE) or next(iter(entry.values()), key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text
