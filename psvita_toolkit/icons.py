"""!
@file icons.py
@brief Elegant unicode glyphs and symbols for the TUI (no colored emojis).
@details
Provides clean, consistent terminal icons that render reliably across macOS,
Linux, and various terminal emulators without wide-character misalignment or
color clashes typical of emojis.
"""


class Icons:
    # Navigation & Selection
    POINTER = "›"
    POINTER_BOLD = "❯"
    POINTER_ACTIVE = "▶"
    BULLET = "•"
    BULLET_HOLLOW = "◦"
    CHECK = "✓"
    CROSS = "✗"
    WARN = "!"
    INFO = "ℹ"
    DOT = "·"
    ARROW_R = "→"
    ARROW_L = "←"
    ARROW_U = "↑"
    ARROW_D = "↓"
    SEARCH = "⌕"
    STAR = "★"
    STAR_EMPTY = "☆"

    # Status badges
    OK = "[✓]"
    FAIL = "[✗]"
    WARNING = "[!]"
    STEP = "[*]"
    PLUS = "[+]"
    MINUS = "[-]"

    # Feature & Category badges (clean unicode glyphs, no emojis)
    BUILD = "⚙"
    DEPLOY = "▲"
    LOGS = "≡"
    CRASH = "⚡"
    SHADERS = "◈"
    LIVEAREA = "▣"
    UTILS = "✦"
    PROFILES = "◆"
    SETTINGS = "⚙"
    PROJECT = "❖"
    DOCTOR = "✚"
    JNI = "λ"
    DOCS = "§"
    TEST = "▶"
    SYNC = "⇄"
    ECOSYSTEM = "☵"
    AI = "✧"
    PATCH = "⌬"
    PACKAGE = "■"
    EXIT = "⏻"
