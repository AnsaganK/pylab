def _normalize(text: str):
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def outputs_match(expected: str, got: str) -> bool:
    """Сравнение как в ICPC: пробелы в конце строк и пустые строки в конце не важны."""
    return _normalize(expected) == _normalize(got)
