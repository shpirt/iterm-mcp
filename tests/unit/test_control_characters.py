import pytest

from iterm_mcp.infrastructure.control_characters import control_code


@pytest.mark.parametrize(
    ("value", "expected"),
    [("C", 3), ("c", 3), ("]", 29), ("ESC", 27), ("escape", 27), ("Z", 26)],
)
def test_control_code(value: str, expected: int) -> None:
    assert control_code(value) == expected


@pytest.mark.parametrize("value", ["", "123", "AB", "control-c"])
def test_control_code_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError, match="Invalid control character letter"):
        control_code(value)
