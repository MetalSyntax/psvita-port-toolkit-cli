"""!
@file i18n.py
@brief Minimal in-memory internationalization (i18n) engine for the toolkit.
@details No external dependencies (no gettext/.po files) -- a single in-memory
         dict is enough for this tool's volume of text.

Each module declares its own strings dict, namespaced by module so keys never
collide across files, and registers it once at import time:

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

Values with variables use `str.format()` syntax, e.g. `{"es": "  Juego:    {name}", ...}`
passed as `t("init_port.summary_game", name=game_name)`.

See `docs/dev-notes/i18n.md` for the full usage guide and the language
bootstrap ordering.
"""

SUPPORTED_LANGUAGES = ("es", "en", "pt")
LANGUAGE_NAMES = {"es": "Español", "en": "English", "pt": "Português"}
DEFAULT_LANGUAGE = "es"

_lang = DEFAULT_LANGUAGE
_catalog = {}


def register(strings):
    """!
    @brief Merge a module's translation dict into the global catalog.
    @param strings dict mapping namespaced keys (e.g. `"modulename.something"`)
           to `{lang_code: text}` dicts.
    @note Each module should call this once, at import time, with its own
          dict. If two modules register the same key, the last one to
          register wins -- hence the module-name-prefix namespacing
          convention.
    """
    _catalog.update(strings)


def set_language(code):
    """!
    @brief Set the active UI language.
    @param code Language code to activate. Ignored if not in `SUPPORTED_LANGUAGES`.
    """
    global _lang
    if code in SUPPORTED_LANGUAGES:
        _lang = code


def get_language():
    """!
    @brief Get the currently active language code.
    @return One of `SUPPORTED_LANGUAGES`.
    """
    return _lang


def t(key, **kwargs):
    """!
    @brief Translate `key` to the active language.
    @param key Namespaced string key (e.g. `"config.language_prompt"`).
    @param kwargs Optional `str.format()` substitution values for the translated text.
    @return The translated string. If `key` isn't registered, returns `key`
            itself. If the active language's translation is missing, falls
            back to `DEFAULT_LANGUAGE`, then to any available translation.
    @note If `kwargs` is given but formatting fails (missing/extra
          placeholder), returns the unformatted text rather than raising.
    """
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
