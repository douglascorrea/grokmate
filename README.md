# grokmate

CLI tool to control the [Grok](https://grok.x.ai) Android app via ADB, with session management backed by SQLite.

## Why?

Grok doesn't have a public API. This tool automates the Android app directly using `uiautomator2` to send messages and read responses programmatically.

## Prerequisites

- **Python 3.11+**
- **ADB** installed and on `PATH` (`adb devices` should list your phone)
- **USB debugging** enabled on your Android device
- **Grok app** (`ai.x.grok`) installed on the device
- **scrcpy** (optional) — for the `check` command's screen mirror status

## Install

```bash
# Clone the repo
git clone https://github.com/YOUR_USER/grokmate.git
cd grokmate

# Install in development mode
pip install -e ".[dev]"

# Or with pipx for isolated install
pipx install .
```

## Quickstart

```bash
# 1. Verify everything is connected
grokmate check

# 2. Start a new chat session
grokmate session new --name "elixir-chat"

# 3. Send a message and get the response
grokmate message "What is the Elixir programming language?"

# 4. Send another message in the same session
grokmate message "How does it compare to Go?"

# 5. One-shot mode (no session management needed)
grokmate message "What's the weather like on Mars?" --one-shot
```

### Session Management

```bash
# Create a named session
grokmate session new --name "my-research"

# Resume a previous session
grokmate session resume --session "my-research"

# Resume by UUID prefix
grokmate session resume --session "a1b2"
```

## How It Works

1. **grokmate** connects to your Android device via ADB
2. Uses `uiautomator2` to interact with the Grok app's UI elements
3. Messages are injected directly into the text field (bypassing the keyboard)
4. Responses are read from the UI accessibility tree (not OCR)
5. Sessions and messages are stored in a local SQLite database

## Known Limitations

### Session Recovery is Local-Metadata-Only

When you `session resume`, grokmate restores its own local tracking context (which session to log messages under). **It does not navigate Grok's UI back to that conversation.** Grok doesn't expose per-conversation URLs or IDs, so there's no reliable way to restore a specific chat.

In practice: after resuming, the next `message` command will send to whatever conversation Grok currently has open. Use `session resume` primarily for organizing your local message history.

### Samsung Autocorrect Gotcha

**This is the whole reason `uiautomator2` is used instead of `adb shell input text`.**

On Samsung Galaxy devices with HoneyBoard (Samsung's default keyboard), `adb shell input text` goes through the keyboard's input pipeline, which means Samsung's autocorrect can and will mangle your text:

- "actual" → "atual"
- Characters get dropped randomly
- Special characters may be interpreted differently

By using `uiautomator2`'s `.set_text()` method, we write directly into the `EditText` widget, completely bypassing the keyboard and its autocorrect. This is reliable across all devices.

## Configuration

grokmate stores its data in `~/.grokmate/`:

```
~/.grokmate/
├── grokmate.db    # SQLite database (sessions + messages)
└── state.json     # Current active session tracking
```

## Running Tests

### Unit tests (no device needed)

```bash
# Run all unit tests
pytest tests/unit/ -v

# With coverage
pytest tests/unit/ -v --cov=grokmate --cov-report=term-missing
```

### E2E tests (requires a connected Android device with Grok)

```bash
# Run e2e tests (will skip if no device connected)
pytest tests/e2e/ -m e2e -v
```

### All tests

```bash
pytest -v
```

## Project Structure

```
grokmate/
├── __init__.py     # Package metadata
├── cli.py          # Typer CLI commands (check, session, message)
├── adb.py          # ADB helpers (device discovery, app launch)
├── grok.py         # Grok UI automation (send, read, new chat)
├── db.py           # SQLite CRUD (sessions, messages)
└── state.py        # ~/.grokmate/state.json management
```

## License

MIT
