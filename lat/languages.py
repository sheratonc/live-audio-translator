"""Language table shared by speech recognition and every translation provider.

Codes are ISO-639-1 style (what Whisper and Google mostly use). Providers that
need different codes (DeepL, Microsoft, ...) convert them in translators.py.
"""

AUTO = "auto"

# (display name, code)
LANGUAGES = [
    ("Arabic", "ar"),
    ("Bulgarian", "bg"),
    ("Catalan", "ca"),
    ("Chinese (Simplified)", "zh-CN"),
    ("Chinese (Traditional)", "zh-TW"),
    ("Croatian", "hr"),
    ("Czech", "cs"),
    ("Danish", "da"),
    ("Dutch", "nl"),
    ("English", "en"),
    ("Estonian", "et"),
    ("Filipino", "tl"),
    ("Finnish", "fi"),
    ("French", "fr"),
    ("German", "de"),
    ("Greek", "el"),
    ("Hebrew", "he"),
    ("Hindi", "hi"),
    ("Hungarian", "hu"),
    ("Indonesian", "id"),
    ("Italian", "it"),
    ("Japanese", "ja"),
    ("Korean", "ko"),
    ("Latvian", "lv"),
    ("Lithuanian", "lt"),
    ("Malay", "ms"),
    ("Norwegian", "no"),
    ("Persian", "fa"),
    ("Polish", "pl"),
    ("Portuguese", "pt"),
    ("Portuguese (Brazil)", "pt-BR"),
    ("Romanian", "ro"),
    ("Russian", "ru"),
    ("Serbian", "sr"),
    ("Slovak", "sk"),
    ("Slovenian", "sl"),
    ("Spanish", "es"),
    ("Swedish", "sv"),
    ("Tamil", "ta"),
    ("Thai", "th"),
    ("Turkish", "tr"),
    ("Ukrainian", "uk"),
    ("Urdu", "ur"),
    ("Vietnamese", "vi"),
    ("English (UK)", "en-GB"),
]
LANGUAGES.sort(key=lambda x: x[0])

NAME_TO_CODE = {name: code for name, code in LANGUAGES}
CODE_TO_NAME = {code: name for name, code in LANGUAGES}

SOURCE_CHOICES = ["Auto-detect"] + [n for n, c in LANGUAGES if c not in ("pt-BR", "en-GB", "zh-TW")]
TARGET_CHOICES = [n for n, _ in LANGUAGES]


def base(code):
    """'zh-CN' -> 'zh', 'pt-BR' -> 'pt'."""
    return code.split("-")[0].lower() if code else code


def to_whisper(code):
    """Language code Whisper understands, or None for auto-detect."""
    if not code or code == AUTO:
        return None
    b = base(code)
    return {"tl": "tl", "no": "no", "he": "he"}.get(b, b)


def from_whisper(code):
    """Whisper's detected language -> our code."""
    if not code:
        return None
    return {"zh": "zh-CN", "jw": "jv", "haw": "haw"}.get(code, code)


def source_name_to_code(name):
    return AUTO if name in (None, "", "Auto-detect") else NAME_TO_CODE.get(name, AUTO)
