from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Sequence, TextIO

from .story_state import StorySessionRecord, StorySnapshotRecord, StoryStateStore, StoryTurnRecord


ChatRole = Literal["player", "narrator"]

PLAYER_LABEL = "Player"
NARRATOR_LABEL = "Narrator"


@dataclass(frozen=True, slots=True)
class ConsoleMessage:
    role: ChatRole
    content: str
    turn_number: int
    created_at: str
    scene_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConsoleTheme:
    player_label: str = "\x1b[1;38;5;214m"
    narrator_label: str = "\x1b[1;38;5;45m"
    meta: str = "\x1b[2;38;5;245m"
    content: str = "\x1b[0m"
    reset: str = "\x1b[0m"


DEFAULT_THEME = ConsoleTheme()


def load_session_messages(
    store: StoryStateStore,
    session_id: int,
    *,
    limit: int | None = None,
) -> list[ConsoleMessage]:
    turns = store.list_turns(session_id, limit=limit)
    turns = list(reversed(turns))
    messages: list[ConsoleMessage] = []

    for turn in turns:
        snapshot = _load_snapshot(store, turn)
        scene_id = snapshot.scene_id if snapshot is not None else None
        messages.append(
            ConsoleMessage(
                role="player",
                content=turn.player_input,
                turn_number=turn.turn_number,
                created_at=turn.created_at,
                scene_id=scene_id,
            ),
        )
        messages.append(
            ConsoleMessage(
                role="narrator",
                content=turn.narrator_output,
                turn_number=turn.turn_number,
                created_at=turn.created_at,
                scene_id=scene_id,
            ),
        )

    return messages


def render_session_history(
    store: StoryStateStore,
    session_id: int,
    *,
    use_color: bool = True,
    theme: ConsoleTheme = DEFAULT_THEME,
) -> str:
    session = store.load_session(session_id)
    if session is None:
        raise KeyError(f"Unknown session: {session_id}")

    messages = load_session_messages(store, session_id)
    header = _render_header(session, len(messages), use_color=use_color, theme=theme)
    body_lines: list[str] = []
    for index, message in enumerate(messages):
        if index and message.role == "player":
            body_lines.append("")
        body_lines.append(_render_message(message, use_color=use_color, theme=theme))
    body = "\n".join(body_lines)

    if body:
        return f"{header}\n{body}\n"
    return f"{header}\n"


def render_message(
    message: ConsoleMessage,
    *,
    use_color: bool = True,
    theme: ConsoleTheme = DEFAULT_THEME,
) -> str:
    return _render_message(message, use_color=use_color, theme=theme)


def stream_message(
    message: ConsoleMessage,
    *,
    use_color: bool = True,
    theme: ConsoleTheme = DEFAULT_THEME,
    writer: TextIO | None = None,
    chunk_delay: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    rendered = _render_message(message, use_color=use_color, theme=theme)
    target = writer or sys.stdout
    if message.role != "narrator":
        target.write(rendered + "\n")
        target.flush()
        return rendered

    chunk_delay = _resolve_chunk_delay(chunk_delay, target)
    lines = rendered.split("\n", 1)
    if len(lines) != 2:
        target.write(rendered + "\n")
        target.flush()
        return rendered

    meta_line, content_line = lines
    prefix, separator, content = content_line.partition(" | ")
    if not separator:
        target.write(rendered + "\n")
        target.flush()
        return rendered

    target.write(meta_line + "\n")
    target.write(prefix + " | ")
    target.flush()
    _write_streamed_text(target, content, chunk_delay=chunk_delay, sleep=sleep)
    target.write("\n")
    target.flush()
    return rendered


def render_message_batch(
    messages: Sequence[ConsoleMessage],
    *,
    use_color: bool = True,
    theme: ConsoleTheme = DEFAULT_THEME,
) -> str:
    body_lines: list[str] = []
    for index, message in enumerate(messages):
        if index and message.role == "player":
            body_lines.append("")
        body_lines.append(_render_message(message, use_color=use_color, theme=theme))
    return "\n".join(body_lines)


def _load_snapshot(store: StoryStateStore, turn: StoryTurnRecord) -> StorySnapshotRecord | None:
    if turn.state_snapshot_id is None:
        return None
    return store.load_snapshot(turn.state_snapshot_id)


def _render_header(
    session: StorySessionRecord,
    message_count: int,
    *,
    use_color: bool,
    theme: ConsoleTheme,
) -> str:
    created_at = _format_timestamp(session.created_at)
    parts = [
        f"Session {session.id}",
        f"{message_count} messages",
        f"started {created_at}",
    ]
    if session.current_scene_id:
        parts.append(f"scene {session.current_scene_id}")
    line = " | ".join(parts)
    return f"{theme.meta}{line}{theme.reset}" if use_color else line


def _render_message(
    message: ConsoleMessage,
    *,
    use_color: bool,
    theme: ConsoleTheme,
) -> str:
    label_style = theme.player_label if message.role == "player" else theme.narrator_label
    label = PLAYER_LABEL if message.role == "player" else NARRATOR_LABEL
    timestamp = _format_timestamp(message.created_at)
    meta_parts = [f"turn {message.turn_number}", timestamp]
    if message.scene_id:
        meta_parts.append(f"scene {message.scene_id}")
    meta = " | ".join(meta_parts)
    prefix = f"{label:<8}"

    if use_color:
        prefix = f"{label_style}{prefix}{theme.reset}"
        meta = f"{theme.meta}{meta}{theme.reset}"
        content = f"{theme.content}{message.content}{theme.reset}"
        return f"{meta}\n{prefix} | {content}"

    return f"{meta}\n{prefix} | {message.content}"


def _resolve_chunk_delay(chunk_delay: float | None, writer: TextIO) -> float:
    if chunk_delay is not None:
        return max(0.0, chunk_delay)
    if hasattr(writer, "isatty") and writer.isatty():
        return 0.01
    return 0.0


def _write_streamed_text(
    writer: TextIO,
    text: str,
    *,
    chunk_delay: float,
    sleep: Callable[[float], None],
) -> None:
    if chunk_delay <= 0:
        writer.write(text)
        writer.flush()
        return

    for chunk in _chunk_text(text):
        writer.write(chunk)
        writer.flush()
        sleep(chunk_delay)


def _chunk_text(text: str) -> list[str]:
    if not text:
        return [""]
    chunks: list[str] = []
    token = []
    for char in text:
        token.append(char)
        if char.isspace():
            chunks.append("".join(token))
            token = []
    if token:
        chunks.append("".join(token))
    return chunks


def _format_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
