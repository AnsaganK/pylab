import secrets

_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
    # казахские буквы
    "ә": "a", "ғ": "g", "қ": "q", "ң": "n", "ө": "o", "ұ": "u", "ү": "u", "һ": "h", "і": "i",
}


def translit(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch in _MAP:
            out.append(_MAP[ch])
        elif ch.isascii() and ch.isalnum():
            out.append(ch)
    return "".join(out)


def make_login(full_name: str) -> str:
    """«Сейтқали Ансаған Ерланұлы» -> «seitqali.a»"""
    parts = [p for p in full_name.split() if p]
    if not parts:
        return "student"
    base = translit(parts[0]) or "student"
    if len(parts) > 1 and translit(parts[1]):
        base += "." + translit(parts[1])[0]
    return base


# Без похожих символов (0/O, 1/l/I), чтобы студенты не путались при вводе
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def make_password(length=8) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))
