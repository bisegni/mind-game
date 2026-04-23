from __future__ import annotations

from dataclasses import dataclass
from textwrap import wrap
from typing import Any, Mapping, Sequence

from .story_state import StorySnapshotRecord


@dataclass(frozen=True, slots=True)
class SceneFrame:
    width: int
    height: int
    lines: tuple[str, ...]
    scene_id: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True, slots=True)
class SceneTheme:
    border: str = "\x1b[2;38;5;245m"
    label: str = "\x1b[1;38;5;81m"
    value: str = "\x1b[0m"
    accent: str = "\x1b[1;38;5;214m"
    reset: str = "\x1b[0m"


DEFAULT_SCENE_THEME = SceneTheme()


def render_scene_frame(
    snapshot: StorySnapshotRecord | Mapping[str, Any],
    *,
    width: int = 72,
    height: int = 20,
    use_color: bool = True,
    theme: SceneTheme = DEFAULT_SCENE_THEME,
) -> SceneFrame:
    data = _coerce_snapshot(snapshot)
    inner_width = max(20, width - 4)
    inner_height = max(4, height - 2)

    sections: list[str] = []
    state = data.get("state")
    if not isinstance(state, Mapping):
        state = data
    scene_id = _text(data.get("scene_id") or _lookup(state, "scene_id")) or "unknown"
    summary_text = _text(data.get("summary_text") or _lookup(state, "summary_text")) or "No scene summary available."
    facts = _sorted_items(_lookup(state, "facts"))
    notes = _string_list(_lookup(state, "notes"))
    observations = _observations(_lookup(state, "observations"))
    focus_ids = _focus_entity_ids(data.get("graph_focus") or _lookup(state, "graph_focus"))

    sections.extend(_label_lines("scene", [scene_id], inner_width))
    sections.extend(_label_lines("summary", _wrap_text(summary_text, inner_width), inner_width))

    if facts:
        sections.extend(_label_lines("facts", [], inner_width))
        for key, value in facts:
            sections.extend(_label_value_lines(key, value, inner_width))

    if notes:
        sections.extend(_label_lines("notes", [], inner_width))
        for note in notes:
            sections.extend(_label_lines("-", _wrap_text(note, inner_width - 2), inner_width))

    if observations:
        sections.extend(_label_lines("observations", [], inner_width))
        for tool, result in observations:
            observation_text = f"{tool} -> {result}" if result else tool
            sections.extend(_label_lines("-", _wrap_text(observation_text, inner_width - 2), inner_width))

    if focus_ids:
        focus_text = ", ".join(str(entity_id) for entity_id in focus_ids)
        sections.extend(_label_lines("focus", [f"entity_ids: {focus_text}"], inner_width))

    clipped_sections = _clip_lines(sections, inner_height)
    body = [_format_line(line, inner_width, use_color=use_color, theme=theme) for line in clipped_sections]
    top_border = _border_line(inner_width, use_color=use_color, theme=theme)
    bottom_border = top_border
    lines = (top_border, *body, bottom_border)
    return SceneFrame(width=width, height=height, lines=tuple(lines), scene_id=scene_id)


def render_scene_text(
    snapshot: StorySnapshotRecord | Mapping[str, Any],
    *,
    width: int = 72,
    height: int = 20,
    use_color: bool = True,
    theme: SceneTheme = DEFAULT_SCENE_THEME,
) -> str:
    return render_scene_frame(
        snapshot,
        width=width,
        height=height,
        use_color=use_color,
        theme=theme,
    ).text


def _coerce_snapshot(snapshot: StorySnapshotRecord | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(snapshot, StorySnapshotRecord):
        return {
            "scene_id": snapshot.scene_id,
            "summary_text": snapshot.summary_text,
            "state": dict(snapshot.state),
            "graph_focus": dict(snapshot.graph_focus),
        }
    return snapshot


def _lookup(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return None


def _sorted_items(value: Any) -> list[tuple[str, str]]:
    if not isinstance(value, Mapping):
        return []
    return [(str(key), _stringify(value[key])) for key in sorted(value)]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _observations(value: Any) -> list[tuple[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray, str)):
        return []
    result: list[tuple[str, str]] = []
    for observation in value:
        if not isinstance(observation, Mapping):
            continue
        tool = _text(observation.get("tool"))
        payload = _text(observation.get("result"))
        if tool:
            result.append((tool, payload or ""))
    return result


def _focus_entity_ids(value: Any) -> list[int]:
    if not isinstance(value, Mapping):
        return []
    raw_ids = value.get("entity_ids", [])
    if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (bytes, bytearray, str)):
        return []
    entity_ids: list[int] = []
    for raw_id in raw_ids:
        try:
            entity_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue
    return entity_ids


def _wrap_text(text: str, width: int) -> list[str]:
    wrapped = wrap(
        text,
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
        drop_whitespace=False,
    )
    return wrapped or [""]


def _label_lines(label: str, values: Sequence[str], width: int) -> list[str]:
    if not values:
        return [f"{label}:"]

    if label == "-":
        lines: list[str] = []
        for value in values:
            lines.extend(_wrap_with_prefix(f"- {value}", width))
        return lines

    lines: list[str] = []
    prefix = f"{label}: "
    first_value = values[0]
    first_line = f"{prefix}{first_value}" if first_value else f"{label}:"
    lines.extend(_wrap_with_prefix(first_line, width))
    for value in values[1:]:
        lines.extend(_wrap_with_prefix(f"  {value}", width))
    return lines


def _label_value_lines(label: str, value: str, width: int) -> list[str]:
    value_width = max(1, width - len(label) - 2)
    wrapped = wrap(
        value,
        width=value_width,
        break_long_words=False,
        break_on_hyphens=False,
        drop_whitespace=False,
    )
    if not wrapped:
        wrapped = [""]
    lines = [f"{label}: {wrapped[0]}".rstrip()]
    for continuation in wrapped[1:]:
        lines.append(f"  {continuation}".rstrip())
    return lines


def _wrap_with_prefix(text: str, width: int) -> list[str]:
    if len(text) <= width:
        return [text]
    wrapped = wrap(
        text,
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
        drop_whitespace=False,
    )
    return wrapped or [text[:width]]


def _clip_lines(lines: Sequence[str], height: int) -> list[str]:
    if len(lines) <= height:
        return list(lines)
    if height <= 1:
        return ["..."][:height]
    clipped = list(lines[: height - 1])
    clipped.append("...")
    return clipped


def _format_line(line: str, width: int, *, use_color: bool, theme: SceneTheme) -> str:
    padded = line[:width].ljust(width)
    if not use_color:
        return f"| {padded} |"

    if line.startswith("scene:"):
        content = f"{theme.accent}{padded}{theme.reset}"
    elif line.endswith(":") or line.startswith("facts:") or line.startswith("notes:") or line.startswith("observations:") or line.startswith("focus:"):
        content = f"{theme.label}{padded}{theme.reset}"
    elif line == "...":
        content = f"{theme.border}{padded}{theme.reset}"
    else:
        content = f"{theme.value}{padded}{theme.reset}"
    return f"{theme.border}| {content} |{theme.reset}"


def _border_line(width: int, *, use_color: bool, theme: SceneTheme) -> str:
    border = "+" + "-" * (width + 2) + "+"
    if not use_color:
        return border
    return f"{theme.border}{border}{theme.reset}"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _stringify(value: Any) -> str:
    if isinstance(value, Mapping):
        return ", ".join(f"{key}={_stringify(value[key])}" for key in sorted(value))
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return ", ".join(_stringify(item) for item in value)
    return _text(value)
