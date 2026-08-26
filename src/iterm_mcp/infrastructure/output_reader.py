"""Output normalization shared by iTerm2 adapters."""


def normalize_lines(lines: list[object]) -> str:
    """Convert iTerm2 LineContents objects to the legacy plain-text buffer."""

    return "\n".join(str(getattr(line, "string", line)) for line in lines).strip()
