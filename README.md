# iterm-mcp
A Model Context Protocol server that provides access to your iTerm2 session through the iTerm2 Python API.

![Main Image](.github/images/demo.gif)

### Features

**Efficient Token Use:** iterm-mcp gives the model the ability to inspect only the output that the model is interested in. The model typically only wants to see the last few lines of output even for long running commands. 

**Natural Integration:** You share iTerm with the model. You can ask questions about what's on the screen, or delegate a task to the model and watch as it performs each step.

**Full Terminal Control and REPL support:** The model can start and interact with REPL's as well as send control characters like ctrl-c, ctrl-z, etc.

**Easy on the Dependencies:** iterm-mcp uses a small Python runtime managed by uv and is designed to be easy to add to Claude Desktop and other MCP clients.


## Safety Considerations

* The user is responsible for using the tool safely.
* No built-in restrictions: iterm-mcp makes no attempt to evaluate the safety of commands that are executed.
* Models can behave in unexpected ways. The user is expected to monitor activity and abort when appropriate.
* For multi-step tasks, you may need to interrupt the model if it goes off track. Start with smaller, focused tasks until you're familiar with how the model behaves. 

### Tools
- `write_to_terminal` - Writes to the active iTerm terminal, often used to run a command. Returns the number of lines of output produced by the command.
- `read_terminal_output` - Reads the requested number of lines from the active iTerm terminal.
- `send_control_character` - Sends a control character to the active iTerm terminal.

### Requirements

* macOS and iTerm2 must be running
* Python 3.11 or greater
* iTerm2 Python API must be enabled in iTerm2 settings
* The first external Python API connection may require macOS Automation permission


## Installation

To use with Claude Desktop, add the server config:

On macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
On Windows: `%APPDATA%/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "iterm-mcp": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/absolute/path/to/iterm-mcp",
        "iterm-mcp"
      ]
    }
  }
}
```

## Development

Install dependencies and create the lock file:
```bash
uv sync
```

Build the server:
```bash
uv run python -m build
```

Run the server over MCP stdio:
```bash
uv run iterm-mcp
```

### Debugging

Since MCP servers communicate over stdio, debugging can be challenging. Use the [MCP Inspector](https://github.com/modelcontextprotocol/inspector):

```bash
npx -y @modelcontextprotocol/inspector
```

Point the Inspector at `uv run --project /absolute/path/to/iterm-mcp iterm-mcp`.

Run quality checks:

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```
