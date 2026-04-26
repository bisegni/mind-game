from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import Input, RichLog, Static

from .console import load_session_messages
from .engine import BaseReActEngine, EngineTurn
from .prompt import is_exit_command
from .story_state import StoryStateStore


RESIZE_REDRAW_DEBOUNCE_SECONDS = 1.0


class MindGameApp(App[None]):
    """Full-screen terminal UI for the interactive game loop."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #dashboard {
        height: 40%;
        min-height: 10;
    }

    #status {
        width: 21;
        min-width: 21;
        border: solid $accent;
        padding: 0;
        text-wrap: nowrap;
        overflow: hidden;
    }

    #situation {
        border: solid $primary;
        padding: 0 1;
    }

    #chat_stack {
        height: 1fr;
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
        self.spinner_index = 0
        self._resize_timer: Timer | None = None
        self._last_redraw_size: tuple[int, int] = (0, 0)

    def compose(self) -> ComposeResult:
        with Horizontal(id="dashboard"):
            yield Static(id="status")
            yield Static(id="situation")
        with Vertical(id="chat_stack"):
            yield RichLog(id="chat", wrap=True, markup=False, auto_scroll=True)
            yield Input(placeholder="Player >", id="player_input")

    def on_mount(self) -> None:
        self._load_initial_chat()
        self.current_scene_ascii = self._latest_scene_ascii()
        self._refresh_dashboard()
        self.set_interval(0.2, self._tick_spinner)
        self.query_one("#player_input", Input).focus()

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
        self.status_mode = "model thinking"
        self.status_message = "waiting for model reply"
        self.is_waiting_for_model = True
        self.spinner_index = 0
        event.input.disabled = True
        self._update_engine_scene_viewport_hint()
        self._refresh_dashboard()
        self.run_worker(lambda: self._run_turn(text), thread=True, exclusive=True)

    def _run_turn(self, text: str) -> None:
        try:
            turn = self.engine.run_turn(text)
        except Exception as error:  # pragma: no cover - defensive UI path
            self.call_from_thread(self._show_error, error)
            return
        self.call_from_thread(self._finish_turn, turn)

    def _finish_turn(self, turn: EngineTurn) -> None:
        self._append_chat("Narrator", turn.reply)
        if turn.scene_ascii:
            self.current_scene_ascii = turn.scene_ascii
        else:
            self.current_scene_ascii = self._latest_scene_ascii()
        if turn.observations:
            self.status_mode = "tool_call"
            self.status_message = turn.observations[-1].tool
        else:
            self.status_mode = "idle"
            self.status_message = "ready"
        self.is_waiting_for_model = False
        self._last_redraw_size = self._scene_viewport_size()
        self._enable_player_input()
        self._refresh_dashboard()

    def _show_error(self, error: Exception) -> None:
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

    def _tick_spinner(self) -> None:
        if not self.is_waiting_for_model:
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
        cols, rows = self._scene_viewport_size()
        art = self.current_scene_ascii.strip() or self._placeholder_scene()
        return _styled_map_text(
            render_scene_map(art, cols=cols, rows=max(3, rows - 1)),
        )

    def _scene_viewport_size(self) -> tuple[int, int]:
        situation = self.query_one("#situation", Static)
        width = max(40, situation.size.width)
        height = max(8, situation.size.height)
        return width, height

    def _update_engine_scene_viewport_hint(self) -> None:
        cols, rows = self._scene_viewport_size()
        setattr(
            self.engine,
            "scene_viewport_size",
            {
                "cols": cols,
                "rows": max(1, rows - 1),
            },
        )

    def _display_status_mode(self) -> str:
        if self.is_waiting_for_model:
            spinner = self.SPINNER_FRAMES[self.spinner_index % len(self.SPINNER_FRAMES)]
            return f"thinking {spinner}"
        return self.status_mode

    def _latest_scene_ascii(self) -> str:
        if self.story_store is None or self.engine.story_session_id is None:
            return ""
        snapshot = self.story_store.latest_snapshot(self.engine.story_session_id)
        if snapshot is None:
            return ""
        return str(snapshot.state.get("scene_ascii") or "")

    def _placeholder_scene(self) -> str:
        summary = ""
        if self.story_store is not None and self.engine.story_session_id is not None:
            snapshot = self.story_store.latest_snapshot(self.engine.story_session_id)
            if snapshot is not None:
                summary = snapshot.summary_text
        return summary or "Map will appear after the next scene."

    def _schedule_scene_redraw(self) -> None:
        if not hasattr(self.engine, "redraw_scene"):
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
        cols, rows = self._scene_viewport_size()
        viewport_rows = max(1, rows - 1)
        if (cols, viewport_rows) == self._last_redraw_size:
            return
        if not hasattr(self.engine, "redraw_scene"):
            return
        self._last_redraw_size = (cols, viewport_rows)
        self.is_waiting_for_model = True
        self.status_mode = "redraw"
        self.status_message = "redrawing map for new size"
        self.spinner_index = 0
        self.query_one("#player_input", Input).disabled = True
        self._update_engine_scene_viewport_hint()
        self._refresh_dashboard()
        viewport = {"cols": cols, "rows": viewport_rows}
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
    """Frame the LLM-supplied scene art to fit the viewport without synthetic fillers."""
    inner_width = max(8, cols - 2)
    target_rows = max(3, rows)
    border = "+" + "=" * inner_width + "+"
    body_capacity = max(1, target_rows - 2)

    raw_lines = [line.rstrip() for line in art.splitlines()]
    if not raw_lines:
        raw_lines = [""]

    body: list[str] = []
    for line in raw_lines[:body_capacity]:
        body.append("|" + line[:inner_width].ljust(inner_width) + "|")
    while len(body) < body_capacity:
        body.append("|" + " " * inner_width + "|")

    return "\n".join([border, *body, border])


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


def _styled_map_text(art: str) -> Text:
    text = Text()
    text.append("MAP / SITUATION\n", style="bold bright_cyan")
    for line in art.splitlines():
        text.append_text(_styled_map_line(line))
        text.append("\n")
    text.rstrip()
    return text


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
    if "thinking" in mode or mode == "tool_call":
        return "bright_yellow"
    return "bright_green"
