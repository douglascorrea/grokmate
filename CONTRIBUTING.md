# Contributing to grokmate

Thanks for your interest in contributing! This guide covers the development workflow, testing, and conventions used in the project.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/douglascorrea/grokmate.git
cd grokmate

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

## Running Tests

### Unit tests (no device needed)

```bash
pytest tests/unit/ -v
```

Unit tests mock all `uiautomator2` and ADB interactions. They should always pass without a connected device.

### E2E tests (requires a real Android device)

```bash
pytest tests/e2e/ -m e2e -v
```

E2E tests interact with a real device running the Grok app. They'll skip automatically if no device is connected.

### With coverage

```bash
pytest tests/unit/ -v --cov=grokmate --cov-report=term-missing
```

## Adding a New Command

1. **Define the CLI command** in `grokmate/cli.py` using Typer decorators.
2. **Implement the logic** in the appropriate module:
   - UI automation (sending, reading, navigation) → `grokmate/grok.py`
   - ADB operations (device discovery, app launching) → `grokmate/adb.py`
   - Data persistence (sessions, messages) → `grokmate/db.py`
   - State tracking → `grokmate/state.py`
3. **Write unit tests** in `tests/unit/test_<module>.py`, mocking device interactions.
4. **Update the README** commands reference table if you added a user-facing command.

## uiautomator2 Selector Pattern

When locating UI elements, always use the **3-selector fallback** pattern:

```python
# Try 1: full package-prefixed resource ID
el = d(resourceId=f"ai.x.grok:id/{RESOURCE_ID}")
if el.exists():
    return el

# Try 2: bare resource ID (some u2 versions accept this)
el = d(resourceId=RESOURCE_ID)
if el.exists():
    return el

# Try 3: class-based fallback
el = d(className="android.widget.EditText")
if el.exists():
    return el
```

This pattern handles differences across uiautomator2 versions and React Native's bare resource ID style. Always try the most specific selector first and fall back to broader ones.

## PR Checklist

Before opening a pull request, please verify:

- [ ] **All unit tests pass** — `pytest tests/unit/ -v`
- [ ] **No API keys, secrets, or device-specific paths** committed
- [ ] **README updated** if you added or changed any user-facing commands
- [ ] **New code has tests** — especially UI automation logic in `grok.py`
- [ ] **Commit messages are clear** — describe *what* and *why*, not just *how*

## Code Style

- Type hints on all public function signatures
- Docstrings on public functions (module, class, and function level)
- Use `from __future__ import annotations` for modern type syntax
- Keep modules focused: CLI wiring in `cli.py`, UI logic in `grok.py`, etc.

## Questions?

Open an issue on [GitHub](https://github.com/douglascorrea/grokmate/issues) or reach out in the project discussions.
