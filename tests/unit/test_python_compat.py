from __future__ import annotations


def test_server_imports_on_supported_python() -> None:
    import iterm_mcp.server

    assert iterm_mcp.server.mcp.name == "iterm-mcp"
