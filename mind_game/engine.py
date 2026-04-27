from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence

from .diagnostics import get_logger
from .story_state import StoryStateStore


logger = get_logger(__name__)
MAP_CONTEXT_LOG_LIMIT = 500
MAP_STREAM_GENERATED_TOKEN_BUDGET_FACTOR = 3

Role = Literal["player", "assistant", "tool"]
DecisionKind = Literal["tool", "final"]


@dataclass(slots=True)
class GameMessage:
    role: Role
    content: str


@dataclass(slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ReActDecision:
    kind: DecisionKind
    content: str = ""
    scene_description: str = ""
    scene_ascii: str = ""
    tool: ToolCall | None = None
    usage: "TokenUsage | None" = None


@dataclass(slots=True)
class ToolObservation:
    tool: str
    result: str


@dataclass(frozen=True, slots=True)
class TokenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    generated_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class StreamChunk:
    content: str = ""
    usage: TokenUsage | None = None


@dataclass(slots=True)
class GameSession:
    turn: int = 0
    facts: dict[str, str] = field(default_factory=dict)
    transcript: list[GameMessage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ToolContext:
    session: GameSession
    player_input: str


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    handler: Callable[[ToolContext, Mapping[str, Any]], str]


@dataclass(frozen=True, slots=True)
class SubagentTask:
    role: str
    task: str
    context: Mapping[str, Any]


class SubagentRunner(Protocol):
    def run(self, task: SubagentTask) -> str:
        ...


class Reasoner(Protocol):
    def decide(self, snapshot: Mapping[str, Any], tools: Sequence[Tool]) -> ReActDecision:
        ...


@dataclass(slots=True)
class EngineTurn:
    player_input: str
    reply: str
    observations: list[ToolObservation]
    scene_description: str = ""
    scene_ascii: str = ""
    usage: TokenUsage | None = None


class BaseReActEngine:
    def __init__(
        self,
        reasoner: Reasoner,
        *,
        story_store: StoryStateStore | None = None,
        session_id: int | None = None,
        subagent_runner: SubagentRunner | None = None,
        tools: Sequence[Tool] | None = None,
        max_steps: int = 6,
    ) -> None:
        self._reasoner = reasoner
        self._subagent_runner = subagent_runner or DeterministicSubagentRunner()
        self._story_store = story_store
        self._story_session_id = session_id
        self._tools = list(tools or self._build_default_tools())
        self._tool_index = {tool.name: tool for tool in self._tools}
        self._max_steps = max_steps
        self.scene_viewport_size: Mapping[str, int] | None = None

        if self._story_store is not None:
            if self._story_session_id is None:
                latest_session = self._story_store.latest_playable_session()
                self._story_session_id = (
                    latest_session.id if latest_session is not None else self._story_store.create_session()
                )

            story_session = self._story_store.load_session(self._story_session_id)
            if story_session is None:
                raise KeyError(f"Unknown story session: {self._story_session_id}")

            compact_state = self._story_store.build_prompt_state(
                self._story_session_id,
                player_input="",
                observations=[],
            )
            self.session = GameSession(
                turn=int(compact_state.get("turn", story_session.current_turn)),
                facts=dict(compact_state.get("facts", {})),
                notes=list(compact_state.get("notes", [])),
            )
        else:
            self.session = GameSession()

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools)

    @property
    def story_session_id(self) -> int | None:
        return self._story_session_id

    def run_turn(self, player_input: str) -> EngineTurn:
        text = player_input.strip()
        if not text:
            raise ValueError("player_input must not be empty")

        self.session.transcript.append(GameMessage(role="player", content=text))
        context = ToolContext(session=self.session, player_input=text)
        observations: list[ToolObservation] = []

        for _ in range(self._max_steps):
            snapshot = self._snapshot(context, observations)
            decision = self._reasoner.decide(snapshot, self._tools)

            if decision.kind == "tool":
                if decision.tool is None:
                    raise ValueError("tool decisions must include a tool call")

                try:
                    result = self._dispatch_tool(context, decision.tool)
                except (KeyError, ValueError) as error:
                    result = f"tool_error: {error}"
                observations.append(ToolObservation(tool=decision.tool.name, result=result))
                continue

            reply = decision.content.strip()
            scene_description = decision.scene_description.strip() or reply
            scene_description_source = "model" if decision.scene_description.strip() else "narration_fallback"
            scene_ascii = decision.scene_ascii.strip()
            logger.info(
                "story scene_description source=%s chars=%s",
                scene_description_source,
                len(scene_description),
            )
            self.session.transcript.append(GameMessage(role="assistant", content=reply))
            self.session.turn += 1
            if self._story_store is not None and self._story_session_id is not None:
                self._story_store.record_turn(
                    self._story_session_id,
                    turn_number=self.session.turn - 1,
                    player_input=text,
                    narrator_output=reply,
                    prompt_state=snapshot,
                    facts=dict(self.session.facts),
                    notes=list(self.session.notes),
                    observations=observations,
                    consequences=[observation.result for observation in observations if observation.result],
                    scene_description=scene_description,
                    scene_ascii=scene_ascii,
                )
            return EngineTurn(
                player_input=text,
                reply=reply,
                observations=observations,
                scene_description=scene_description,
                scene_ascii=scene_ascii,
                usage=decision.usage,
            )

        raise RuntimeError("ReAct loop exceeded the configured step limit")

    def stream_map(
        self,
        *,
        viewport: Mapping[str, int],
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """Stream a fresh scene ASCII from the reasoner, calling on_chunk with growing buffer."""
        self.scene_viewport_size = dict(viewport)
        context = ToolContext(session=self.session, player_input="")
        snapshot = self._snapshot(context, [])
        snapshot["redraw_only"] = True
        snapshot["player_input"] = ""
        if not hasattr(self._reasoner, "stream_map"):
            return ""
        _log_map_context(snapshot, viewport)
        accumulated = ""
        viewport_size = _viewport_size(viewport)
        raw_budget = _streamed_map_budget(viewport_size)
        for raw_chunk in self._reasoner.stream_map(snapshot, viewport):
            chunk = _coerce_stream_chunk(raw_chunk)
            accumulated += chunk.content
            visible = _normalize_streamed_map_for_viewport(accumulated, viewport_size, final=False)
            if on_chunk is not None:
                if chunk.usage is None:
                    on_chunk(visible)
                else:
                    on_chunk(StreamChunk(content=visible, usage=chunk.usage))
            if (
                _streamed_map_is_complete(accumulated, viewport_size)
                or len(accumulated) >= raw_budget
                or _streamed_map_usage_exhausted(chunk.usage, viewport_size)
            ):
                break
        final = _normalize_streamed_map_for_viewport(accumulated, viewport_size, final=True).rstrip("\n")
        if _has_raw_wall_spam(accumulated, viewport_size) or _is_degenerate_map(final, viewport_size):
            logger.warning("map stream produced degenerate output; using fallback map")
            final = _fallback_scene_map(snapshot, viewport_size)
        if final and self._story_store is not None and self._story_session_id is not None:
            self._story_store.update_latest_scene_ascii(self._story_session_id, final)
        return final

    def redraw_scene(self, *, viewport: Mapping[str, int]) -> str:
        """Ask the reasoner for a fresh scene_ascii sized to the new viewport.

        Routes through stream_map when the reasoner supports streaming.
        Falls back to a single decide() call otherwise.
        """
        if hasattr(self._reasoner, "stream_map"):
            return self.stream_map(viewport=viewport)
        self.scene_viewport_size = dict(viewport)
        context = ToolContext(session=self.session, player_input="")
        snapshot = self._snapshot(context, [])
        snapshot["redraw_only"] = True
        snapshot["player_input"] = ""
        decision = self._reasoner.decide(snapshot, self._tools)
        if decision.kind != "final":
            return ""
        return decision.scene_ascii.strip()

    def _build_default_tools(self) -> list[Tool]:
        return [
            Tool(
                name="session.read",
                description="Return a compact snapshot of the current session state.",
                handler=self._tool_session_read,
            ),
            Tool(
                name="session.write_fact",
                description="Store a short fact in the shared session state.",
                handler=self._tool_session_write_fact,
            ),
            Tool(
                name="session.add_note",
                description="Append a compact note to session memory.",
                handler=self._tool_session_add_note,
            ),
            Tool(
                name="subagent.delegate",
                description="Delegate a bounded task to a focused subagent.",
                handler=self._tool_delegate_subagent,
            ),
        ]

    def _snapshot(self, context: ToolContext, observations: Sequence[ToolObservation]) -> dict[str, Any]:
        if self._story_store is not None and self._story_session_id is not None:
            snapshot = self._story_store.build_prompt_state(
                self._story_session_id,
                player_input=context.player_input,
                observations=observations,
            )
            snapshot["turn"] = self.session.turn
            snapshot["facts"] = dict(self.session.facts)
            snapshot["notes"] = list(self.session.notes[-6:])
            snapshot["player_input"] = context.player_input
            snapshot["observations"] = [
                {"tool": item.tool, "result": item.result}
                for item in observations
            ]
            snapshot["tool_catalog"] = [
                {"name": tool.name, "description": tool.description}
                for tool in self._tools
            ]
            if self.scene_viewport_size:
                snapshot["scene_viewport"] = dict(self.scene_viewport_size)
            return snapshot

        snapshot = {
            "turn": self.session.turn,
            "player_input": context.player_input,
            "facts": dict(self.session.facts),
            "recent_messages": [
                {"role": message.role, "content": message.content}
                for message in self.session.transcript[-6:]
            ],
            "notes": list(self.session.notes[-6:]),
            "observations": [
                {"tool": item.tool, "result": item.result}
                for item in observations
            ],
            "tool_catalog": [
                {"name": tool.name, "description": tool.description}
                for tool in self._tools
            ],
        }
        if self.scene_viewport_size:
            snapshot["scene_viewport"] = dict(self.scene_viewport_size)
        return snapshot

    def _dispatch_tool(self, context: ToolContext, call: ToolCall) -> str:
        tool = self._tool_index.get(call.name)
        if tool is None:
            raise KeyError(f"Unknown tool: {call.name}")

        return tool.handler(context, call.arguments)

    def _tool_session_read(self, context: ToolContext, arguments: Mapping[str, Any]) -> str:
        payload = self._snapshot(context, [])
        payload = {
            "turn": payload.get("turn", self.session.turn),
            "facts": payload.get("facts", dict(self.session.facts)),
            "notes": payload.get("notes", list(self.session.notes[-3:])),
            "player_input": payload.get("player_input", context.player_input),
            "summary_text": payload.get("summary_text", ""),
            "scene_description": payload.get("scene_description", ""),
            "graph_focus": payload.get("graph_focus", {}),
        }
        return json.dumps(payload, sort_keys=True)

    def _tool_session_write_fact(self, context: ToolContext, arguments: Mapping[str, Any]) -> str:
        key = str(arguments.get("key", "")).strip()
        value = str(arguments.get("value", "")).strip()
        if not key:
            raise ValueError("session.write_fact requires a non-empty key")
        self.session.facts[key] = value
        return json.dumps({"stored": key, "value": value}, sort_keys=True)

    def _tool_session_add_note(self, context: ToolContext, arguments: Mapping[str, Any]) -> str:
        note = str(arguments.get("note", "")).strip()
        if not note:
            raise ValueError("session.add_note requires a non-empty note")
        self.session.notes.append(note)
        return json.dumps({"added": note}, sort_keys=True)

    def _tool_delegate_subagent(self, context: ToolContext, arguments: Mapping[str, Any]) -> str:
        task = str(arguments.get("task", "")).strip()
        role = str(arguments.get("role", "general")).strip() or "general"
        if not task:
            raise ValueError("subagent.delegate requires a non-empty task")

        bounded_context = arguments.get("context")
        if not isinstance(bounded_context, Mapping):
            bounded_context = {
                "player_input": context.player_input,
                "facts": dict(self.session.facts),
                "notes": list(self.session.notes[-3:]),
            }

        result = self._subagent_runner.run(
            SubagentTask(role=role, task=task, context=bounded_context),
        )
        self.session.notes.append(f"{role}: {result}")
        return result


class DeterministicSubagentRunner:
    def run(self, task: SubagentTask) -> str:
        preview = json.dumps(task.context, sort_keys=True)
        return f"{task.role} handled {task.task} with {preview[:120]}"


def _coerce_stream_chunk(value: Any) -> StreamChunk:
    if isinstance(value, StreamChunk):
        return value
    usage = getattr(value, "usage", None)
    content = getattr(value, "content", value)
    return StreamChunk(
        content=content if isinstance(content, str) else str(content or ""),
        usage=usage if isinstance(usage, TokenUsage) else None,
    )


def _log_map_context(snapshot: Mapping[str, Any], viewport: Mapping[str, int]) -> None:
    viewport_size = _viewport_size(viewport)
    viewport_text = f"{viewport_size[0]}x{viewport_size[1]}" if viewport_size is not None else str(dict(viewport))
    scene_description = str(snapshot.get("scene_description") or "").strip()
    summary_text = str(snapshot.get("summary_text") or "")
    recent_messages = snapshot.get("recent_messages")
    recent_count = len(recent_messages) if isinstance(recent_messages, Sequence) and not isinstance(recent_messages, (str, bytes, bytearray)) else 0
    logger.info(
        'map context viewport=%s scene_description_present=%s scene_description="%s" summary_chars=%s recent_messages=%s',
        viewport_text,
        bool(scene_description),
        _one_line_log_text(scene_description, limit=MAP_CONTEXT_LOG_LIMIT),
        len(summary_text),
        recent_count,
    )


def _one_line_log_text(value: str, *, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def _viewport_size(viewport: Mapping[str, int]) -> tuple[int, int] | None:
    try:
        cols = int(viewport.get("cols") or 0)
        rows = int(viewport.get("rows") or 0)
    except (TypeError, ValueError):
        return None
    if cols <= 0 or rows <= 0:
        return None
    return cols, rows


def _normalize_streamed_map_for_viewport(text: str, viewport: tuple[int, int] | None, *, final: bool) -> str:
    if viewport is None:
        return text
    cols, rows = viewport
    raw_lines = text.splitlines()[:rows]
    if final:
        visible_lines = raw_lines + [""] * max(0, rows - len(raw_lines))
    else:
        visible_lines = raw_lines
    return "\n".join(_normalize_map_line(line, cols) for line in visible_lines)


def _streamed_map_is_complete(text: str, viewport: tuple[int, int] | None) -> bool:
    if viewport is None:
        return False
    _, rows = viewport
    lines = text.splitlines()
    return len(lines) >= rows


def _streamed_map_budget(viewport: tuple[int, int] | None) -> int:
    if viewport is None:
        return 4096
    cols, rows = viewport
    return max(cols * rows * 2, 1024)


def _streamed_map_usage_exhausted(usage: TokenUsage | None, viewport: tuple[int, int] | None) -> bool:
    if usage is None or usage.generated_tokens is None or viewport is None:
        return False
    cols, rows = viewport
    return usage.generated_tokens >= max(128, (cols + 1) * rows * MAP_STREAM_GENERATED_TOKEN_BUDGET_FACTOR)


def _normalize_map_line(line: str, cols: int) -> str:
    clipped = line[:cols]
    if _is_wall_fill_line(clipped, cols) or _is_wall_spam_line(clipped, cols):
        clipped = ""
    return clipped + "." * (cols - len(clipped))


def _is_wall_fill_line(line: str, cols: int) -> bool:
    if cols < 8:
        return False
    stripped = line.strip()
    if not stripped or set(stripped) != {"#"}:
        return False
    return len(stripped) >= max(8, int(cols * 0.75))


def _is_wall_spam_line(line: str, cols: int) -> bool:
    if cols < 16:
        return False
    non_floor = [char for char in line if char != "."]
    if not non_floor:
        return False
    wall_count = sum(1 for char in non_floor if char == "#")
    if wall_count < max(8, int(cols * 0.35)):
        return False
    useful_count = sum(1 for char in line if char in "@*?~[]ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    return useful_count == 0


def _is_degenerate_map(art: str, viewport: tuple[int, int] | None) -> bool:
    lines = art.splitlines()
    if not lines:
        return True
    text = "\n".join(lines)
    if viewport is None:
        cols = max(len(line) for line in lines)
    else:
        cols, _ = viewport
    wall_spam_rows = sum(1 for line in lines if _is_wall_spam_line(line, cols) or _is_wall_fill_line(line, cols))
    if wall_spam_rows >= max(2, len(lines) // 3):
        return True
    wall_chars = sum(line.count("#") for line in lines)
    total_chars = sum(len(line) for line in lines)
    useful_chars = sum(1 for char in text if char in "@*?~[]ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    return total_chars > 0 and wall_chars / total_chars > 0.45 and useful_chars == 0


def _has_raw_wall_spam(text: str, viewport: tuple[int, int] | None) -> bool:
    lines = text.splitlines()
    if not lines:
        return False
    cols = viewport[0] if viewport is not None else max(len(line) for line in lines)
    if cols < 16:
        return False
    wall_spam_rows = sum(1 for line in lines if _is_wall_spam_line(line[:cols], cols) or _is_wall_fill_line(line[:cols], cols))
    return wall_spam_rows >= max(2, len(lines) // 3)


def _fallback_scene_map(snapshot: Mapping[str, Any], viewport: tuple[int, int] | None) -> str:
    cols, rows = viewport or (40, 12)
    cols = max(8, cols)
    rows = max(4, rows)
    grid = [["." for _ in range(cols)] for _ in range(rows)]
    center_x = cols // 2
    center_y = rows // 2
    _put_text(grid, center_x, center_y, "@")

    description = str(snapshot.get("scene_description") or snapshot.get("summary_text") or "").lower()
    if "console" in description:
        _put_text(grid, center_x - 2, max(0, center_y - 3), "*CON")
    if "panel" in description:
        _put_text(grid, 1, center_y, "*PAN")
    if "device" in description:
        _put_text(grid, max(0, cols - 5), center_y, "*DEV")
    if "corridor" in description or "exit" in description or "behind" in description:
        exit_y = min(rows - 1, center_y + 3)
        if exit_y <= center_y + 2 and rows > center_y + 1:
            exit_y = rows - 1
        _put_text(grid, center_x, exit_y, "?")
        for y in range(center_y + 1, max(center_y + 1, exit_y)):
            grid[y][center_x] = "|"
    return "\n".join("".join(row) for row in grid)


def _put_text(grid: list[list[str]], x: int, y: int, text: str) -> None:
    if y < 0 or y >= len(grid):
        return
    width = len(grid[y])
    for index, char in enumerate(text):
        column = x + index
        if 0 <= column < width:
            grid[y][column] = char
