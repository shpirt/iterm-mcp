"""Validation and conversion for terminal control characters."""


def control_code(letter: str) -> int:
    normalized = letter.strip().upper()
    if normalized == "]":
        return 29
    if normalized in {"ESC", "ESCAPE"}:
        return 27
    if len(normalized) == 1 and "A" <= normalized <= "Z":
        return ord(normalized) - 64
    raise ValueError("Invalid control character letter")
