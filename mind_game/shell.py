from __future__ import annotations

"""Terminal split-pane shell primitives.

The shell is intentionally renderer-agnostic: it lays out a fixed-width
status rail on the left and an independently refreshed scene viewport on the
right, but it only depends on a generic scene frame object. A future renderer
can supply those frames without coupling story state to terminal layout.
"""

import re
from dataclasses import dataclass
from typing import Literal, Sequence


ShellMode = Literal["idle", "typing", "thinking", "tool_call", "spinner", "error"]

SPINNER_FRAMES = ("|", "/", "-", "\\")
ANSI_RESET = "\x1b[0m"
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True, slots=True)
class ShellStatus:
    mode: ShellMode = "idle"
    turn_number: int | None = None
    scene_id: str | None = None
    model_name: str | None = None
    tool_name: str | None = None
    message: str | None = None
    error: str | None = None
    spinner_index: int = 0


@dataclass(frozen=True, slots=True)
class SceneFrame:
    title: str | None = None
    subtitle: str | None = None
    lines: Sequence[str] = ()


def render_split_pane(
    status: ShellStatus,
    scene_frame: SceneFrame,
    *,
    width: int,
    height: int,
    rail_width: int = 24,
    use_color: bool = True,
) -> str:
    if rail_width < 1:
        raise ValueError("rail_width must be at least 1")
    if width <= rail_width + 1:
        raise ValueError("width must leave room for both panes")
    if height < 1:
        raise ValueError("height must be at least 1")

    scene_width = width - rail_width - 1
    left_lines = render_status_rail(status, width=rail_width, height=height, use_color=use_color)
    right_lines = render_scene_viewport(scene_frame, width=scene_width, height=height, use_color=use_color)
    return "\n".join(f"{left}|{right}" for left, right in zip(left_lines, right_lines))


def render_status_rail(
    status: ShellStatus,
    *,
    width: int,
    height: int,
    use_color: bool = True,
) -> list[str]:
    if width < 1:
        raise ValueError("width must be at least 1")
    if height < 1:
        raise ValueError("height must be at least 1")

    lines = [
        _format_header("STATUS", width, use_color=use_color, tone="strong"),
        _format_field("turn", str(status.turn_number) if status.turn_number is not None else "idle", width),
        _format_field("scene", status.scene_id or "-", width),
    ]

    if status.model_name:
        lines.append(_format_field("model", status.model_name, width))

    lines.append(_format_mode_line(status, width, use_color=use_color))

    summary = status.message or ""
    if status.mode == "error":
        summary = status.error or summary
    if summary:
        lines.append(_format_field("note", summary, width))

    return _pad_and_clip(lines, width=width, height=height)


def render_scene_viewport(
    scene_frame: SceneFrame,
    *,
    width: int,
    height: int,
    use_color: bool = True,
) -> list[str]:
    if width < 1:
        raise ValueError("width must be at least 1")
    if height < 1:
        raise ValueError("height must be at least 1")

    lines: list[str] = []
    if scene_frame.title:
        lines.append(_format_header(f"SCENE: {scene_frame.title}", width, use_color=use_color, tone="accent"))
    if scene_frame.subtitle:
        lines.append(_format_field("info", scene_frame.subtitle, width))
    lines.extend(_clip_text(line, width) for line in scene_frame.lines)
    return _pad_and_clip(lines, width=width, height=height)


def _format_mode_line(status: ShellStatus, width: int, *, use_color: bool) -> str:
    if status.mode == "idle":
        return _format_field("state", "idle", width)
    if status.mode == "typing":
        return _format_field("state", "typing", width)
    if status.mode == "thinking":
        return _format_field("state", "thinking", width, use_color=use_color, tone="accent")
    if status.mode == "tool_call":
        label = status.tool_name or "tool"
        return _format_field("tool", label, width, use_color=use_color, tone="accent")
    if status.mode == "spinner":
        spinner = SPINNER_FRAMES[status.spinner_index % len(SPINNER_FRAMES)]
        detail = status.message or "loading"
        return _format_field("load", f"{spinner} {detail}", width, use_color=use_color, tone="accent")
    if status.mode == "error":
        return _format_field("error", status.error or "unknown", width, use_color=use_color, tone="error")
    return _format_field("state", status.mode, width)


def _format_header(text: str, width: int, *, use_color: bool, tone: str) -> str:
    text = _clip_text(text, width)
    if not use_color:
        return text
    if tone == "accent":
        return f"\x1b[1;38;5;45m{text}\x1b[0m"
    if tone == "error":
        return f"\x1b[1;38;5;203m{text}\x1b[0m"
    return f"\x1b[1;38;5;214m{text}\x1b[0m"


def _format_field(label: str, value: str, width: int, *, use_color: bool = False, tone: str = "default") -> str:
    text = _clip_text(f"{label}: {value}", width)
    if not use_color:
        return text
    if tone == "accent":
        return f"\x1b[38;5;45m{text}\x1b[0m"
    if tone == "error":
        return f"\x1b[38;5;203m{text}\x1b[0m"
    return text


def _pad_and_clip(lines: Sequence[str], *, width: int, height: int) -> list[str]:
    rendered = [_clip_text(line, width) for line in lines[:height]]
    while len(rendered) < height:
        rendered.append(" " * width)
    return rendered


def _clip_text(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if "\x1b[" not in text:
        if len(text) > width:
            return text[:width]
        return text.ljust(width)

    rendered: list[str] = []
    visible = 0
    active = False
    index = 0

    while index < len(text) and visible < width:
        if text[index] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, index)
            if match is not None:
                sequence = match.group(0)
                rendered.append(sequence)
                active = sequence != ANSI_RESET
                index = match.end()
                continue

        rendered.append(text[index])
        visible += 1
        index += 1

    if visible < width:
        padding = " " * (width - visible)
        if rendered and rendered[-1] == ANSI_RESET:
            rendered.pop()
            rendered.append(padding)
            rendered.append(ANSI_RESET)
        else:
            rendered.append(padding)
            if active:
                rendered.append(ANSI_RESET)
    elif active and (not rendered or rendered[-1] != ANSI_RESET):
        rendered.append(ANSI_RESET)

    return "".join(rendered)
