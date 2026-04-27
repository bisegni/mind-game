from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.timer import Timer
from textual.widgets import Input, RichLog, Static

from .console import load_session_messages
from .diagnostics import get_logger
from .engine import BaseReActEngine, EngineTurn, StreamChunk, TokenUsage
from .prompt import is_exit_command
from .story_state import StoryStateStore


RESIZE_REDRAW_DEBOUNCE_SECONDS = 1.0
MAP_STREAM_REQUEST_TIMEOUT_SECONDS = 12.0
LLM_VIEWPORT_MAX_COLS = 120
LLM_VIEWPORT_MAX_ROWS = 18
LLM_VIEWPORT_MAX_RATIO = 2
LLM_VIEWPORT_MIN_COLS = 24
LLM_VIEWPORT_MIN_ROWS = 8


logger = get_logger(__name__)


class MindGameApp(App[None]):
    """Full-screen terminal UI for the interactive game loop."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #status {
        width: 1fr;
        height: 7;
        border: solid $accent;
        padding: 0 1;
        text-wrap: nowrap;
        overflow: hidden;
    }

    #situation {
        width: 1fr;
        height: 1fr;
        border: solid $primary;
        padding: 0 1;
        text-wrap: nowrap;
        overflow: hidden;
    }

    #scene_description {
        width: 1fr;
        height: 3;
        border: solid $secondary;
        padding: 0 1;
        overflow: hidden;
    }

    #chat_stack {
        height: 40%;
        min-height: 6;
    }

    #chat {
        height: 1fr;
        border: solid $surface;
        padding: 0 1;
    }

    #player_input {
        height: 3;
    }

    #player_input:disabled {
        color: $text-muted;
        border: solid $surface;
        background: $surface;
    }
    """

    SPINNER_FRAMES = ("|", "/", "-", "\\")

    def __init__(
        self,
        *,
        engine: BaseReActEngine,
        story_store: StoryStateStore | None,
        model_name: str,
        base_url: str,
    ) -> None:
        super().__init__()
        self.engine = engine
        self.story_store = story_store
        self.model_name = model_name
        self.base_url = base_url
        self.status_mode = "idle"
        self.status_message = "ready"
        self.current_scene_ascii = ""
        self.is_waiting_for_model = False
        self.is_streaming_map = False
        self.spinner_index = 0
        self._map_stream_viewport: tuple[int, int] | None = None
        self._map_stream_line_count = 0
        self._map_stream_usage: TokenUsage | None = None
        self._resize_timer: Timer | None = None
        self._map_timeout_timer: Timer | None = None
        self._last_redraw_size: tuple[int, int] = (0, 0)
        self._pending_map_viewport: dict[str, int] | None = None
        self._map_worker: object | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="status")
        yield Static(id="situation")
        yield Static(id="scene_description")
        with Vertical(id="chat_stack"):
            yield RichLog(id="chat", wrap=True, markup=False, auto_scroll=True)
            yield Input(placeholder="Player >", id="player_input")

    def on_mount(self) -> None:
        logger.info("tui mount model=%s base_url=%s", self.model_name, self.base_url)
        self._load_initial_chat()
        self.current_scene_ascii = self._latest_scene_ascii()
        self._refresh_dashboard()
        self.set_interval(0.2, self._tick_spinner)
        self._update_engine_scene_viewport_hint()
        cols, rows = self._llm_viewport_size()
        self._last_redraw_size = (cols, rows)
        self._start_map_stream({"cols": cols, "rows": rows})
        self.query_one("#player_input", Input).focus()

    def exit(self, *args: object, **kwargs: object) -> None:
        self._prepare_for_shutdown()
        super().exit(*args, **kwargs)

    def _on_exit_app(self, event: object) -> None:
        self._prepare_for_shutdown()
        super()._on_exit_app()

    def on_resize(self) -> None:
        self._update_engine_scene_viewport_hint()
        self._refresh_dashboard()
        self._schedule_scene_redraw()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if not text:
            return
        if is_exit_command(text):
            self.exit()
            return

        self._append_chat("Player", text)
        self._stop_map_stream_status()
        self.status_mode = "queued"
        self.status_message = "story: queued"
        self.is_waiting_for_model = True
        self.spinner_index = 0
        event.input.disabled = True
        self._update_engine_scene_viewport_hint()
        self._refresh_dashboard()
        self.run_worker(lambda: self._run_turn(text), thread=True, exclusive=True)

    def _run_turn(self, text: str) -> None:
        try:
            logger.info("story turn start chars=%s", len(text))
            self.call_from_thread(self._set_story_waiting_status, "story: waiting for narrator")
            turn = self.engine.run_turn(text)
        except Exception as error:  # pragma: no cover - defensive UI path
            logger.exception("story turn failed")
            self.call_from_thread(self._show_error, error)
            return
        logger.info("story turn done reply_chars=%s usage=%s", len(turn.reply), turn.usage)
        self.call_from_thread(self._finish_turn, turn)

    def _set_story_waiting_status(self, message: str = "story: waiting for narrator") -> None:
        if not self.is_waiting_for_model:
            return
        self.status_mode = "story"
        self.status_message = message
        self._refresh_dashboard()

    def _finish_turn(self, turn: EngineTurn) -> None:
        self._append_chat("Narrator", turn.reply)
        if turn.scene_ascii:
            self.current_scene_ascii = turn.scene_ascii
        else:
            self.current_scene_ascii = self._latest_scene_ascii()
        if turn.observations:
            self.status_mode = "tool_call"
            self.status_message = turn.observations[-1].tool
        elif turn.usage is not None:
            self.status_mode = "idle"
            self.status_message = f"story: {_format_token_usage(turn.usage)}"
        else:
            self.status_mode = "idle"
            self.status_message = "ready"
        self.is_waiting_for_model = False
        self._last_redraw_size = self._llm_viewport_size()
        self._enable_player_input()
        self._refresh_dashboard()
        # Start streaming the map in background; player can type while it paints.
        cols, rows = self._llm_viewport_size()
        self._start_map_stream({"cols": cols, "rows": rows})

    def _show_error(self, error: Exception) -> None:
        logger.warning("tui error status set: %s", error)
        self.status_mode = "error"
        self.status_message = str(error)
        self.is_waiting_for_model = False
        self._append_chat("System", f"Error: {error}")
        self._enable_player_input()
        self._refresh_dashboard()

    def _load_initial_chat(self) -> None:
        if self.story_store is None or self.engine.story_session_id is None:
            return
        for message in load_session_messages(self.story_store, self.engine.story_session_id, limit=20):
            label = "Player" if message.role == "player" else "Narrator"
            self._append_chat(label, message.content)

    def _append_chat(self, label: str, text: str) -> None:
        chat = self.query_one("#chat", RichLog)
        chat.write(_styled_chat_line(label, text))

    def _refresh_dashboard(self) -> None:
        self.query_one("#status", Static).update(self._status_text())
        self.query_one("#situation", Static).update(self._situation_text())
        self.query_one("#scene_description", Static).update(self._scene_description_text())

    def _tick_spinner(self) -> None:
        if not self.is_waiting_for_model and not self.is_streaming_map:
            return
        self.spinner_index += 1
        self._refresh_dashboard()

    def _enable_player_input(self) -> None:
        player_input = self.query_one("#player_input", Input)
        player_input.disabled = False
        player_input.focus()

    def _status_text(self) -> Text:
        session = None
        if self.story_store is not None and self.engine.story_session_id is not None:
            session = self.story_store.load_session(self.engine.story_session_id)
        turn = getattr(session, "current_turn", self.engine.session.turn)
        scene = getattr(session, "current_scene_id", None) or "-"
        width = max(12, self.query_one("#status", Static).size.width)
        value_width = max(1, width - len("mdl: "))
        status = Text()
        status.append("STATUS\n", style="bold bright_yellow")
        _append_status_field(status, "t", str(turn), value_width=value_width, value_style="bright_black")
        _append_status_field(
            status,
            "sc",
            _display_scene_id(str(scene)),
            value_width=value_width,
            value_style="bright_black",
        )
        _append_status_field(status, "mdl", self.model_name, value_width=value_width, value_style="bright_black")
        _append_status_field(
            status,
            "st",
            self._display_status_mode(),
            value_width=value_width,
            value_style=_status_style(self.status_mode),
        )
        _append_status_field(
            status,
            "nt",
            self.status_message,
            value_width=value_width,
            value_style=_status_style(self.status_mode),
            newline=False,
        )
        return status

    def _situation_text(self) -> Text:
        cols, rows = self._widget_viewport_size()
        art = self.current_scene_ascii.strip() or self._placeholder_scene()
        return _styled_map_text(
            render_scene_map(art, cols=cols, rows=max(1, rows - 2)),
        )

    def _widget_viewport_size(self) -> tuple[int, int]:
        situation = self.query_one("#situation", Static)
        width = max(20, situation.size.width)
        height = max(3, situation.size.height)
        return width, height

    def _llm_viewport_size(self) -> tuple[int, int]:
        widget_cols, widget_rows = self._widget_viewport_size()
        rows = max(LLM_VIEWPORT_MIN_ROWS, min(widget_rows - 1, LLM_VIEWPORT_MAX_ROWS))
        cols = max(
            LLM_VIEWPORT_MIN_COLS,
            min(widget_cols, LLM_VIEWPORT_MAX_COLS, rows * LLM_VIEWPORT_MAX_RATIO),
        )
        return cols, rows

    def _update_engine_scene_viewport_hint(self) -> None:
        cols, rows = self._llm_viewport_size()
        setattr(
            self.engine,
            "scene_viewport_size",
            {
                "cols": cols,
                "rows": rows,
            },
        )

    def _display_status_mode(self) -> str:
        if self.is_waiting_for_model:
            spinner = self.SPINNER_FRAMES[self.spinner_index % len(self.SPINNER_FRAMES)]
            return f"story {spinner}"
        if self.is_streaming_map:
            spinner = self.SPINNER_FRAMES[self.spinner_index % len(self.SPINNER_FRAMES)]
            return f"map {spinner}"
        return self.status_mode

    def _latest_scene_ascii(self) -> str:
        if self.story_store is None or self.engine.story_session_id is None:
            return ""
        snapshot = self.story_store.latest_snapshot(self.engine.story_session_id)
        if snapshot is None:
            return ""
        return str(snapshot.state.get("scene_ascii") or "")

    def _latest_scene_description(self) -> str:
        if self.story_store is None or self.engine.story_session_id is None:
            return ""
        snapshot = self.story_store.latest_snapshot(self.engine.story_session_id)
        if snapshot is None:
            return ""
        return str(snapshot.state.get("scene_description") or "").strip()

    def _scene_description_text(self) -> Text:
        description = self._latest_scene_description() or "No stored scene description yet."
        width = max(12, self.query_one("#scene_description", Static).size.width)
        value_width = max(1, width - len("desc: "))
        text = Text()
        text.append("SCENE DESCRIPTION\n", style="bold bright_magenta")
        _append_status_field(text, "desc", description, value_width=value_width, value_style="bright_black", newline=False)
        return text

    def _placeholder_scene(self) -> str:
        summary = ""
        if self.story_store is not None and self.engine.story_session_id is not None:
            snapshot = self.story_store.latest_snapshot(self.engine.story_session_id)
            if snapshot is not None:
                summary = snapshot.summary_text
        return summary or "Map will appear after the next scene."

    def _start_map_stream(self, viewport: dict[str, int]) -> None:
        if not hasattr(self.engine, "stream_map"):
            return
        if self.is_waiting_for_model:
            self._pending_map_viewport = dict(viewport)
            return
        if self.is_streaming_map:
            self._pending_map_viewport = dict(viewport)
            return
        cols = int(viewport.get("cols") or 0)
        rows = int(viewport.get("rows") or 0)
        self.is_streaming_map = True
        self._map_stream_viewport = (cols, rows)
        self._map_stream_line_count = 0
        self._map_stream_usage = None
        self.status_mode = "map"
        self.status_message = f"map: queued {cols}x{rows}"
        self.spinner_index = 0
        self._refresh_dashboard()
        self._reset_map_timeout()
        logger.info("map stream queued viewport=%sx%s", cols, rows)
        self._map_worker = self.run_worker(
            lambda: self._run_map_stream(viewport),
            thread=True,
            exclusive=False,
        )

    def _run_map_stream(self, viewport: dict[str, int]) -> None:
        try:
            logger.info("map stream start viewport=%s", viewport)
            self.call_from_thread(self._set_map_requesting_status, viewport)
            final_ascii = self.engine.stream_map(
                viewport=viewport,
                on_chunk=lambda buf: self.call_from_thread(self._apply_partial_scene, buf),
            )
        except Exception as error:  # pragma: no cover - defensive UI path
            logger.exception("map stream failed")
            self.call_from_thread(self._finish_map_error, error)
            return
        logger.info("map stream done viewport=%s", viewport)
        self.call_from_thread(self._finish_map_stream, final_ascii)

    def _set_map_requesting_status(self, viewport: dict[str, int]) -> None:
        if not self.is_streaming_map:
            return
        cols = int(viewport.get("cols") or 0)
        rows = int(viewport.get("rows") or 0)
        self.status_mode = "map"
        self.status_message = f"map: requesting {cols}x{rows}"
        self._reset_map_timeout()
        self._refresh_dashboard()

    def _apply_partial_scene(self, buf: str | StreamChunk) -> None:
        usage = buf.usage if isinstance(buf, StreamChunk) else None
        content = buf.content if isinstance(buf, StreamChunk) else buf
        if self._map_stream_viewport is None and self.status_mode == "error":
            logger.info("ignored stale map chunk after error")
            return
        self._clear_map_timeout()
        if usage is not None:
            self._map_stream_usage = usage
        if content:
            self.current_scene_ascii = _clip_scene_ascii_for_viewport(content, self._map_stream_viewport)
            self._map_stream_line_count = len(self.current_scene_ascii.splitlines())
        if self._map_stream_viewport is not None:
            _, rows = self._map_stream_viewport
            self.status_mode = "map"
            self.status_message = self._map_status_message(rows)
        try:
            self._refresh_dashboard()
        except NoMatches:
            logger.info("ignored late map chunk after ui teardown")

    def _finish_map_stream(self, final_ascii: str = "") -> None:
        if not self.is_streaming_map and self._map_stream_viewport is None:
            logger.info("ignored stale map finish")
            return
        self._clear_map_timeout()
        if final_ascii:
            self.current_scene_ascii = _clip_scene_ascii_for_viewport(final_ascii, self._map_stream_viewport)
        self.is_streaming_map = False
        self._map_stream_viewport = None
        self._map_stream_line_count = 0
        self._map_stream_usage = None
        pending_viewport = self._pending_map_viewport
        self._pending_map_viewport = None
        self._map_worker = None
        if not self.is_waiting_for_model:
            self.status_mode = "idle"
            self.status_message = "ready"
        try:
            self._last_redraw_size = self._llm_viewport_size()
            self._refresh_dashboard()
        except NoMatches:
            logger.info("ignored late map finish after ui teardown")
            return
        if pending_viewport is not None:
            self._start_map_stream(pending_viewport)

    def _finish_map_error(self, error: Exception) -> None:
        if not self.is_streaming_map and self._map_stream_viewport is None:
            logger.info("ignored stale map error: %s", error)
            return
        self._clear_map_timeout()
        self.is_streaming_map = False
        self._map_stream_viewport = None
        self._map_stream_line_count = 0
        self._map_stream_usage = None
        self._pending_map_viewport = None
        self._map_worker = None
        self.status_mode = "error"
        self.status_message = f"map failed: {error}"
        logger.warning("map stream error surfaced: %s", error)
        try:
            self._refresh_dashboard()
        except NoMatches:
            logger.info("ignored late map error after ui teardown")

    def _stop_map_stream_status(self) -> None:
        self._clear_map_timeout()
        self.is_streaming_map = False
        self._map_stream_viewport = None
        self._map_stream_line_count = 0
        self._map_stream_usage = None
        self._pending_map_viewport = None
        if self._map_worker is not None and hasattr(self._map_worker, "cancel"):
            self._map_worker.cancel()
        self._map_worker = None

    def _prepare_for_shutdown(self) -> None:
        logger.info("tui shutdown requested")
        if self._resize_timer is not None:
            self._resize_timer.stop()
            self._resize_timer = None
        self._clear_map_timeout()
        self._stop_map_stream_status()
        self.is_waiting_for_model = False
        if hasattr(self, "workers"):
            self.workers.cancel_all()

    def _reset_map_timeout(self) -> None:
        self._clear_map_timeout()
        self._map_timeout_timer = self.set_timer(
            MAP_STREAM_REQUEST_TIMEOUT_SECONDS,
            self._map_stream_timed_out,
        )

    def _clear_map_timeout(self) -> None:
        if self._map_timeout_timer is not None:
            self._map_timeout_timer.stop()
            self._map_timeout_timer = None

    def _map_stream_timed_out(self) -> None:
        self._map_timeout_timer = None
        if not self.is_streaming_map:
            return
        logger.warning("map stream timed out")
        if self._map_worker is not None and hasattr(self._map_worker, "cancel"):
            self._map_worker.cancel()
        self._finish_map_error(TimeoutError("timed out"))

    def _map_status_message(self, rows: int) -> str:
        message = f"map: streaming {self._map_stream_line_count}/{rows}"
        if self._map_stream_usage is not None:
            message = f"{message} {_format_token_usage(self._map_stream_usage)}"
        return message

    def _schedule_scene_redraw(self) -> None:
        if not hasattr(self.engine, "stream_map") and not hasattr(self.engine, "redraw_scene"):
            return
        if self._resize_timer is not None:
            self._resize_timer.stop()
            self._resize_timer = None
        if self.is_waiting_for_model:
            return
        self._resize_timer = self.set_timer(
            RESIZE_REDRAW_DEBOUNCE_SECONDS,
            self._trigger_scene_redraw,
        )

    def _trigger_scene_redraw(self) -> None:
        self._resize_timer = None
        if self.is_waiting_for_model:
            return
        cols, rows = self._llm_viewport_size()
        if (cols, rows) == self._last_redraw_size:
            return
        if not hasattr(self.engine, "stream_map") and not hasattr(self.engine, "redraw_scene"):
            return
        self._last_redraw_size = (cols, rows)
        self._update_engine_scene_viewport_hint()
        if hasattr(self.engine, "stream_map"):
            self._start_map_stream({"cols": cols, "rows": rows})
        else:
            self.is_waiting_for_model = True
            self.status_mode = "redraw"
            self.status_message = f"map: requesting {cols}x{rows}"
            self.spinner_index = 0
            self.query_one("#player_input", Input).disabled = True
            self._refresh_dashboard()
            viewport = {"cols": cols, "rows": rows}
            self.run_worker(lambda: self._run_redraw(viewport), thread=True, exclusive=True)

    def _run_redraw(self, viewport: dict[str, int]) -> None:
        try:
            ascii_art = self.engine.redraw_scene(viewport=viewport)
        except Exception as error:  # pragma: no cover - defensive UI path
            self.call_from_thread(self._finish_redraw_error, error)
            return
        self.call_from_thread(self._finish_redraw, ascii_art)

    def _finish_redraw(self, ascii_art: str) -> None:
        if ascii_art:
            self.current_scene_ascii = ascii_art
        self.status_mode = "idle"
        self.status_message = "ready"
        self.is_waiting_for_model = False
        self._enable_player_input()
        self._refresh_dashboard()

    def _finish_redraw_error(self, error: Exception) -> None:
        self.status_mode = "error"
        self.status_message = f"redraw failed: {error}"
        self.is_waiting_for_model = False
        self._enable_player_input()
        self._refresh_dashboard()


def render_scene_map(art: str, *, cols: int = 68, rows: int = 16) -> str:
    """Frame the LLM-supplied scene art to fit the viewport, padding empty cells with floor tiles."""
    inner_width = max(8, cols - 2)
    target_rows = max(1, rows)
    border = "+" + "=" * inner_width + "+"
    if target_rows == 1:
        return border
    if target_rows == 2:
        return "\n".join([border, border])

    body_capacity = target_rows - 2

    raw_lines = art.splitlines() or [""]

    body: list[str] = []
    for line in raw_lines[:body_capacity]:
        clipped = _clear_wall_fill_line(line[:inner_width], inner_width)
        padded = clipped + "." * (inner_width - len(clipped))
        body.append("|" + padded + "|")
    while len(body) < body_capacity:
        body.append("|" + "." * inner_width + "|")

    return "\n".join([border, *body, border])


def _clip_scene_ascii_for_viewport(art: str, viewport: tuple[int, int] | None) -> str:
    if viewport is None:
        return art
    cols, rows = viewport
    if cols <= 0 or rows <= 0:
        return art
    return "\n".join(line[:cols] for line in art.splitlines()[:rows])


def _clear_wall_fill_line(line: str, width: int) -> str:
    if width < 8:
        return line
    stripped = line.strip()
    if stripped and set(stripped) == {"#"} and len(stripped) >= max(8, int(width * 0.75)):
        return ""
    non_floor = [char for char in line if char != "."]
    wall_count = sum(1 for char in non_floor if char == "#")
    useful_count = sum(1 for char in line if char in "@*?~[]ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    if width >= 16 and wall_count >= max(8, int(width * 0.35)) and useful_count == 0:
        return ""
    return line


def _format_token_usage(usage: TokenUsage) -> str:
    if usage.completion_tokens is not None and usage.total_tokens is not None:
        return f"tok {usage.completion_tokens}/{usage.total_tokens}"
    if usage.generated_tokens is not None and usage.total_tokens is not None:
        return f"tok {usage.generated_tokens}/{usage.total_tokens}"
    if usage.prompt_tokens is not None and usage.completion_tokens is not None:
        return f"tok {usage.prompt_tokens}/{usage.completion_tokens}"
    if usage.total_tokens is not None:
        return f"tok {usage.total_tokens}"
    if usage.generated_tokens is not None:
        return f"tok {usage.generated_tokens}"
    return "tok -"


def _styled_chat_line(label: str, text: str) -> Text:
    style = {
        "Player": "bold bright_cyan",
        "Narrator": "bold rgb(255,190,100)",
        "System": "bold bright_red",
        "Error": "bold bright_red",
    }.get(label, "bold bright_white")
    body_style = "bright_white" if label != "System" else "bright_red"
    line = Text()
    line.append(label, style=style)
    line.append(": ", style="bright_black")
    line.append(text, style=body_style)
    return line


def _append_status_field(
    status: Text,
    label: str,
    value: str,
    *,
    value_width: int,
    value_style: str = "bright_white",
    newline: bool = True,
) -> None:
    status.append(f"{label}: ", style="bright_cyan")
    status.append(_clip_text(value, value_width), style=value_style)
    if newline:
        status.append("\n")


def _clip_text(value: str, width: int) -> str:
    text = value.replace("\n", " ").strip()
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


def _display_scene_id(scene_id: str) -> str:
    if scene_id.startswith("scene:"):
        return scene_id[len("scene:") :]
    return scene_id


MAP_LEGEND_ITEMS: tuple[tuple[str, str], ...] = (
    ("@", "you"),
    ("#", "wall"),
    (".", "floor"),
    ("*", "point"),
    ("?", "unknown"),
    ("~", "water"),
    ("=|+", "corridor"),
)


def _styled_map_text(art: str) -> Text:
    text = Text()
    text.append("MAP / SITUATION\n", style="bold bright_cyan")
    for line in art.splitlines():
        text.append_text(_styled_map_line(line))
        text.append("\n")
    text.append_text(_styled_legend_line())
    return text


def _styled_legend_line() -> Text:
    line = Text()
    line.append("Legend: ", style="bold bright_cyan")
    for index, (glyphs, label) in enumerate(MAP_LEGEND_ITEMS):
        if index:
            line.append("  ", style="bright_black")
        for char in glyphs:
            line.append(char, style=_map_char_style(char))
        line.append(" ", style="bright_black")
        line.append(label, style="bright_white")
    return line


def _styled_map_line(line: str) -> Text:
    styled = Text()
    for char in line:
        styled.append(char, style=_map_char_style(char))
    return styled


def _map_char_style(char: str) -> str:
    if char == "@":
        return "bold bright_yellow"
    if char in {"~"}:
        return "bright_blue"
    if char in {"#", "=", "-", "|", "+", "/", "\\"}:
        return "bright_cyan"
    if char in {"?", "*", "G", "B", "H", "O", "X", "^"}:
        return "rgb(255,190,100)"
    if char == ".":
        return "bright_black"
    return "bright_white"


def _status_style(mode: str) -> str:
    if mode == "error":
        return "bright_red"
    if mode in {"story", "map", "redraw", "tool_call"}:
        return "bright_yellow"
    return "bright_green"
