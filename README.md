# grokmate

**CLI tool to control the [Grok](https://grok.x.ai) Android app via ADB + uiautomator2.**

Send messages, read responses, and manage chat sessions — all from your terminal.

## Why This Exists

Grok doesn't have a public API. The obvious approach — `adb shell input text` — falls apart on Samsung Galaxy devices because **HoneyBoard** (Samsung's default keyboard) intercepts and autocorrects everything that flows through the input pipeline:

- "actual" → "atual"
- Random character drops
- Special characters reinterpreted

`grokmate` solves this by using **uiautomator2** to call `.set_text()` directly on the `EditText` widget, bypassing the keyboard entirely. No autocorrect. No character mangling. Reliable on every device.

## Requirements

| Requirement | Notes |
|---|---|
| **Python 3.11+** | |
| **ADB** | Installed and on `PATH` — `adb devices` should list your phone |
| **Android device** | USB debugging enabled, physically connected or on the same ADB network |
| **Grok app** (`ai.x.grok`) | Installed on the device |
| **uiautomator2** | Installed automatically as a dependency |
| **Pillow** | Installed automatically as a dependency — used for screencap+crop fallback |

Optional: [scrcpy](https://github.com/Genymobile/scrcpy) for the `check` command's screen mirror status.

## Installation

```bash
# Clone
git clone https://github.com/douglascorrea/grokmate.git
cd grokmate

# Install in development mode
pip install -e ".[dev]"

# Or as an isolated tool via pipx
pipx install .
```

## Quickstart

```bash
# 1. Verify everything is connected
grokmate check

# 2. Start a new chat session
grokmate session new --name my-session

# 3. Send a message and get the response
grokmate message "Your question here"

# 4. One-shot mode — creates a throwaway session, no setup needed
grokmate message "Quick question" --one-shot

# 5. Resume a previous session later
grokmate session resume --session my-session
```

## Commands Reference

| Command | Description | Key Flags |
|---|---|---|
| `grokmate check` | Preflight check — ADB, Grok app, uiautomator2, scrcpy | |
| `grokmate session new` | Create a new Grok chat session | `--name` / `-n` — human-readable name |
| `grokmate session resume` | Resume a previously created session | `--session` / `-s` — name or UUID prefix |
| `grokmate message <text>` | Send a message and print Grok's response | `--one-shot` — throwaway session, no prior setup needed; `--timeout` / `-t` — seconds to wait for response (default: 120); `--no-images` — skip image extraction |

### Image extraction (`message` command)

By default, after printing the text response, `grokmate message` scans the Grok
UI for generated images and saves them locally.  Each extracted image is printed
on its own line with an `IMAGE:` prefix:

```
$ grokmate message "Imagine a sunset over the ocean"
Here is a beautiful image of a sunset...

IMAGE:/Users/you/.grokmate/media/1710000000_grok_img_0.png
```

These `IMAGE:` lines can be captured by other tools (e.g. piped into a Telegram
send command).

Use `--no-images` to skip image extraction entirely:

```bash
grokmate message "Your question" --no-images
```

**How image extraction works:**

1. After the text response is read, grokmate looks for large `ImageView` elements
   (≥ 80 × 80 px) on screen — these are Grok-generated images.
2. **Primary path:** long-press the image → tap *Save image* / *Download* from
   the context menu → `adb pull` the saved file to `~/.grokmate/media/`.
3. **Fallback:** if the context menu doesn't appear or save fails, grokmate takes
   a full-screen screenshot via `adb exec-out screencap -p` and crops it to the
   element's bounds using Pillow.

Extracted images are stored in `~/.grokmate/media/` with filenames of the form
`<timestamp>_grok_img_<index>.png`.

## How It Works

1. Connects to your Android device via ADB
2. Uses uiautomator2 to interact with the Grok app's UI elements
3. Messages are injected directly into the text field (bypassing the keyboard)
4. Responses are read from the UI accessibility tree (TextViews), not OCR
5. Sessions and messages are stored in a local SQLite database at `~/.grokmate/`

## Architecture

The package is organised into focused modules: **`cli.py`** defines the Typer commands, **`grok.py`** handles all UI automation (sending messages, reading responses, tapping new chat), **`adb.py`** provides ADB helpers (device discovery, app launching), **`db.py`** manages SQLite persistence for sessions and messages, and **`state.py`** tracks the currently active session via a JSON file. The CLI layer orchestrates calls between these modules — commands flow from `cli.py` → `grok.py`/`adb.py` → `db.py`.

## Known Limitations

### Session resume is local-metadata-only

`grokmate session resume` restores local tracking context (which session to log messages under). It does **not** navigate Grok's UI back to that specific conversation — Grok doesn't expose per-conversation identifiers or deep links. After resuming, the next `message` command sends to whatever conversation Grok currently has open.

### Samsung Galaxy HoneyBoard autocorrect

This is the core reason `uiautomator2` is used instead of `adb shell input text`. Samsung's HoneyBoard keyboard autocorrects, drops characters, and reinterprets special characters when text flows through the standard input pipeline. By writing directly to the EditText widget via `.set_text()`, we bypass the keyboard entirely.

### Physical or network ADB connection required

The Android device must be connected via USB or on the same ADB network (`adb connect <ip>`). Wireless ADB (Android 11+) works fine.

## Running Tests

### Unit tests (no device needed)

```bash
pytest tests/unit/ -v

# With coverage
pytest tests/unit/ -v --cov=grokmate --cov-report=term-missing
```

### E2E tests (requires a connected Android device with Grok)

```bash
pytest tests/e2e/ -m e2e -v
```

### All tests

```bash
pytest -v
```

## Configuration

Data is stored in `~/.grokmate/`:

```
~/.grokmate/
├── grokmate.db    # SQLite database (sessions + messages)
├── state.json     # Current active session tracking
└── media/         # Extracted images (created on first use)
    └── <timestamp>_grok_img_<n>.png
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing guide, and PR checklist.

## License

MIT
