"""grokmate CLI — control the Grok Android app via ADB."""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from grokmate import adb, db, grok, state

app = typer.Typer(
    name="grokmate",
    help="CLI tool to control the Grok Android app via ADB.",
    no_args_is_help=True,
)
session_app = typer.Typer(help="Session management commands.")
app.add_typer(session_app, name="session")

console = Console()

# Allow overriding paths for testing
_db_path: Path = db.DEFAULT_DB_PATH
_state_path: Path = state.DEFAULT_STATE_PATH


def _get_conn() -> "db.sqlite3.Connection":
    return db.get_connection(_db_path)


# ── check ───────────────────────────────────────────────────────────────────


@app.command()
def check() -> None:
    """Check that all prerequisites are met."""
    table = Table(title="grokmate preflight check")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Details")

    all_ok = True

    # ADB device
    device = adb.get_connected_device()
    if device:
        table.add_row("ADB device", "[green]✓[/green]", device.serial)
    else:
        table.add_row("ADB device", "[red]✗[/red]", "No device connected")
        all_ok = False

    # Grok installed
    serial = device.serial if device else None
    if device and adb.is_grok_installed(serial):
        table.add_row("Grok app", "[green]✓[/green]", adb.GROK_PACKAGE)
    elif device:
        table.add_row("Grok app", "[red]✗[/red]", "Not installed")
        all_ok = False
    else:
        table.add_row("Grok app", "[yellow]?[/yellow]", "Skipped (no device)")

    # uiautomator2
    if device:
        try:
            grok.connect_device(serial)
            table.add_row("uiautomator2", "[green]✓[/green]", "Connected")
        except Exception as e:
            table.add_row("uiautomator2", "[red]✗[/red]", str(e)[:60])
            all_ok = False
    else:
        table.add_row("uiautomator2", "[yellow]?[/yellow]", "Skipped (no device)")

    # scrcpy (optional)
    if adb.scrcpy_available():
        table.add_row("scrcpy", "[green]✓[/green]", "Available")
    else:
        table.add_row("scrcpy", "[yellow]![/yellow]", "Not found (optional)")

    console.print(table)
    raise typer.Exit(code=0 if all_ok else 1)


# ── session new ─────────────────────────────────────────────────────────────


@session_app.command("new")
def session_new(
    name: Optional[str] = typer.Option(
        None, "--name", "-n", help="Human-readable session name."
    ),
) -> None:
    """Create a new Grok chat session."""
    conn = _get_conn()

    # Suspend any currently active sessions
    db.suspend_active_sessions(conn)

    # Generate identity
    session_id = str(uuid.uuid4())
    if not name:
        name = f"session-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    # Get device serial
    device_info = adb.get_connected_device()
    serial = device_info.serial if device_info else None

    # Launch Grok & start new chat
    adb.launch_grok(serial)
    try:
        u2_dev = grok.connect_device(serial)
        grok.tap_new_chat(u2_dev)  # waits for chat_text_input to appear
    except Exception as e:
        console.print(f"[yellow]Warning:[/yellow] Could not tap new chat: {e}")

    # Persist
    db.create_session(conn, session_id, name, device_serial=serial, status="active")
    state.write_current_session(session_id, _state_path)
    conn.close()

    console.print(f"Session [bold]'{name}'[/bold] created (id: {session_id})")


# ── session resume ──────────────────────────────────────────────────────────


@session_app.command("resume")
def session_resume(
    session: str = typer.Option(
        ..., "--session", "-s", help="Session name or UUID prefix to resume."
    ),
) -> None:
    """Resume a previously created session."""
    conn = _get_conn()

    row = db.find_session(conn, session)
    if not row:
        console.print(f"[red]Session '{session}' not found.[/red]")
        raise typer.Exit(code=1)

    # Suspend current active sessions, then activate the target
    db.suspend_active_sessions(conn)
    db.update_session_status(conn, row["id"], "active")
    state.write_current_session(row["id"], _state_path)

    # Bring Grok to foreground
    device_info = adb.get_connected_device()
    serial = device_info.serial if device_info else None
    adb.launch_grok(serial)

    conn.close()
    console.print(f"Resumed session [bold]'{row['name']}'[/bold]")


# ── message ─────────────────────────────────────────────────────────────────


@app.command()
def message(
    text: str = typer.Argument(..., help="Message text to send to Grok."),
    one_shot: bool = typer.Option(
        False, "--one-shot", help="Create a throwaway session for this message."
    ),
    timeout: int = typer.Option(
        120,
        "--timeout",
        "-t",
        help="Seconds to wait for Grok's response before giving up (default: 120).",
    ),
    no_images: bool = typer.Option(
        False,
        "--no-images",
        help=(
            "Skip image extraction. By default, grokmate attempts to save any "
            "generated images from Grok to ~/.grokmate/media/ and prints "
            "IMAGE:/path/to/file lines to stdout."
        ),
    ),
) -> None:
    """Send a message to Grok and print the response.

    If Grok generated images, each image is saved to ~/.grokmate/media/ and
    its path is printed on a separate line prefixed with ``IMAGE:``, e.g.::

        IMAGE:/Users/you/.grokmate/media/1234567890_grok_img_0.png

    Use ``--no-images`` to disable image extraction entirely.
    """
    conn = _get_conn()

    extract_images = not no_images

    if one_shot:
        _message_one_shot(conn, text, timeout=timeout, extract_images=extract_images)
    else:
        _message_in_session(conn, text, timeout=timeout, extract_images=extract_images)

    conn.close()


def _message_in_session(
    conn: "db.sqlite3.Connection",
    text: str,
    *,
    timeout: int = 120,
    extract_images: bool = True,
) -> None:
    session_id = state.read_current_session(_state_path)
    if not session_id:
        console.print(
            "[red]No active session. Run 'grokmate session new' or use --one-shot.[/red]"
        )
        raise typer.Exit(code=1)

    # Verify session exists
    row = db.get_session(conn, session_id)
    if not row:
        console.print(f"[red]Session {session_id} not found in DB.[/red]")
        raise typer.Exit(code=1)

    _send_and_receive(
        conn, session_id, text, timeout=timeout, extract_images=extract_images
    )


def _message_one_shot(
    conn: "db.sqlite3.Connection",
    text: str,
    *,
    timeout: int = 120,
    extract_images: bool = True,
) -> None:
    session_id = str(uuid.uuid4())
    name = f"oneshot-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    device_info = adb.get_connected_device()
    serial = device_info.serial if device_info else None

    # Launch, connect u2, open a fresh chat — all before _send_and_receive
    adb.launch_grok(serial)
    u2_dev = grok.connect_device(serial)
    try:
        grok.tap_new_chat(u2_dev)  # waits for chat_text_input to appear
    except Exception as e:
        console.print(f"[yellow]Warning:[/yellow] Could not tap new chat: {e}")

    db.create_session(conn, session_id, name, device_serial=serial, status="active")
    # Do NOT update state.json — one-shot is isolated

    # Pass already-connected u2_dev so _send_and_receive won't re-launch
    _send_and_receive(
        conn,
        session_id,
        text,
        u2_dev=u2_dev,
        timeout=timeout,
        extract_images=extract_images,
    )

    db.update_session_status(conn, session_id, "oneshot_done")


def _send_and_receive(
    conn: "db.sqlite3.Connection",
    session_id: str,
    text: str,
    u2_dev: object = None,
    timeout: int = 120,
    extract_images: bool = True,
) -> None:
    device_info = adb.get_connected_device()
    serial = device_info.serial if device_info else None

    if u2_dev is None:
        # Only launch + connect when not already provided (in-session path)
        adb.launch_grok(serial)
        u2_dev = grok.connect_device(serial)

    # Send
    grok.send_message(u2_dev, text)
    db.add_message(conn, session_id, "user", text)

    # Wait & read text response
    response = grok.extract_full_response(u2_dev, timeout=timeout)
    db.add_message(conn, session_id, "assistant", response)

    # Print text to stdout
    console.print(response)

    # ── Image extraction ──────────────────────────────────────────────────
    if extract_images:
        try:
            image_paths = grok.extract_images(u2_dev, serial=serial)
        except Exception as exc:
            console.print(
                f"[yellow]Warning:[/yellow] image extraction failed: {exc}",
                file=sys.stderr,
            )
            image_paths = []

        for img_path in image_paths:
            # Print to stdout so callers can capture IMAGE: lines
            print(f"IMAGE:{img_path.resolve()}")
