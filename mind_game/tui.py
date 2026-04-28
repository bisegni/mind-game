from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from .onboarding import OllamaOnboardingReasoner, StoryBible
    from .story_state import OnboardingSessionRecord


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

    TYPEWRITER_WORD_DELAY = 0.04

    def __init__(
        self,
        *,
        engine: BaseReActEngine | None,
        story_store: StoryStateStore | None,
        model_name: str,
        base_url: str,
        reasoner: Any = None,
        onboarding_session: OnboardingSessionRecord | None = None,
        onboarding_reasoner: OllamaOnboardingReasoner | None = None,
    ) -> None:
        super().__init__()
        self.engine = engine
        self._reasoner = reasoner
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
        self._status_turn = self.engine.session.turn if self.engine is not None else 0
        self._status_scene_id = "-"
        self._scene_description = ""
        self._scene_summary = ""
        # Onboarding state
        self._is_onboarding = onboarding_session is not None
        self._onboarding_session = onboarding_session
        self._onboarding_reasoner = onboarding_reasoner
        self._onboarding_lore_text = ""
        self._onboarding_asked_field: str | None = None
        self._onboarding_current_question = ""
        # Animation state
        self._animation_frame: int = 0
        self._map_animator: _MapAnimator | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="status")
        yield Static(id="situation")
        yield Static(id="scene_description")
        with Vertical(id="chat_stack"):
            yield RichLog(id="chat", wrap=True, markup=False, auto_scroll=True)
            yield Input(placeholder="Player >", id="player_input")

    def on_mount(self) -> None:
        logger.info("tui mount model=%s base_url=%s is_onboarding=%s", self.model_name, self.base_url, self._is_onboarding)
        self.set_interval(0.2, self._tick_spinner)
        self.set_interval(0.4, self._animate_scene)
        if self._is_onboarding:
            self._refresh_dashboard()
            input_widget = self.query_one("#player_input", Input)
            input_widget.disabled = True
            self.run_worker(self._onboarding_ask_next, thread=True)
        else:
            self._load_initial_chat()
            self._refresh_story_cache()
            self._refresh_dashboard()
            if self.engine is not None:
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

        if self._is_onboarding:
            self._append_chat("Player", text)
            event.input.disabled = True
            self.run_worker(lambda: self._onboarding_handle_answer(text), thread=True)
            return

        self._append_chat("Player", text)
        self._stop_map_stream_status()
        self.status_mode = "queued"
        self.status_message = "story: queued"
        self.is_waiting_for_model = True
        self.spinner_index = 0
        event.input.disabled = True
        if self.engine is not None:
            self._update_engine_scene_viewport_hint()
        self._refresh_dashboard()
        self.run_worker(lambda: self._run_turn(text), thread=True, exclusive=True)

    def _run_turn(self, text: str) -> None:
        if self.engine is None:
            return
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
        self._status_turn = self.engine.session.turn
        if turn.scene_description:
            self._scene_description = turn.scene_description
        self._map_animator = None
        self._refresh_story_cache()
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
        if self.story_store is None or self.engine is None or self.engine.story_session_id is None:
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

    def _animate_scene(self) -> None:
        if self._is_onboarding or self.is_streaming_map or self.is_waiting_for_model:
            return
        self._animation_frame += 1
        try:
            self.query_one("#situation", Static).update(self._situation_text())
        except NoMatches:
            pass

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
        turn = self._status_turn
        scene = self._status_scene_id
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
        if self._is_onboarding:
            return _styled_lore_text(self._onboarding_lore_text)
        cols, rows = self._widget_viewport_size()
        base_art = self.current_scene_ascii.strip() or self._placeholder_scene()
        if self._map_animator is not None:
            animated_art = self._map_animator.frame(self._animation_frame)
        else:
            animated_art = _animate_map_frame(base_art, self._animation_frame)
        return _styled_map_text(
            render_scene_map(animated_art, cols=cols, rows=max(1, rows - 2)),
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
        if self.engine is None:
            return
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

    def _refresh_story_cache(self) -> None:
        if self.engine is None:
            return
        self._status_turn = self.engine.session.turn
        if self.story_store is None or self.engine.story_session_id is None:
            return
        session = self.story_store.load_session(self.engine.story_session_id)
        if session is not None:
            self._status_turn = session.current_turn
            self._status_scene_id = session.current_scene_id or "-"
        snapshot = self.story_store.latest_snapshot(self.engine.story_session_id)
        if snapshot is None:
            return
        self._scene_description = str(snapshot.state.get("scene_description") or "").strip()
        self._scene_summary = snapshot.summary_text
        latest_ascii = str(snapshot.state.get("scene_ascii") or "")
        if latest_ascii:
            if not self.current_scene_ascii or _has_meaningful_map_content(latest_ascii):
                logger.debug("story cache updated current_scene_ascii chars=%d meaningful=%s", len(latest_ascii), _has_meaningful_map_content(latest_ascii))
                self.current_scene_ascii = latest_ascii
            else:
                logger.debug("story cache skipped low-signal ascii chars=%d existing_chars=%d", len(latest_ascii), len(self.current_scene_ascii))

    def _scene_description_text(self) -> Text:
        description = self._scene_description or "No stored scene description yet."
        width = max(12, self.query_one("#scene_description", Static).size.width)
        value_width = max(1, width - len("desc: "))
        text = Text()
        text.append("SCENE DESCRIPTION\n", style="bold bright_magenta")
        _append_status_field(text, "desc", description, value_width=value_width, value_style="bright_black", newline=False)
        return text

    def _placeholder_scene(self) -> str:
        return self._scene_summary or "Map will appear after the next scene."

    def _start_map_stream(self, viewport: dict[str, int]) -> None:
        if self.engine is None or not hasattr(self.engine, "stream_map"):
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
            clipped = _clip_scene_ascii_for_viewport(content, self._map_stream_viewport)
            self._map_stream_line_count = len(clipped.splitlines())
            if not self.current_scene_ascii or _has_meaningful_map_content(clipped):
                self.current_scene_ascii = clipped
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
            clipped = _clip_scene_ascii_for_viewport(final_ascii, self._map_stream_viewport)
            meaningful = _has_meaningful_map_content(clipped)
            if not self.current_scene_ascii or meaningful:
                logger.info("map stream final applied chars=%d meaningful=%s viewport=%s", len(clipped), meaningful, self._map_stream_viewport)
                self.current_scene_ascii = clipped
                self._map_animator = _MapAnimator(clipped)
            else:
                logger.info("map stream final skipped low-signal chars=%d existing_chars=%d viewport=%s", len(clipped), len(self.current_scene_ascii), self._map_stream_viewport)
        self._refresh_story_cache()
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
        if self.engine is None:
            return
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
        if self.is_waiting_for_model or self.engine is None:
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

    # ------------------------------------------------------------------
    # Onboarding flow
    # ------------------------------------------------------------------

    def _onboarding_typewrite_lore(self, text: str) -> None:
        """Stream text word-by-word into the map/situation area (replaces current lore)."""
        self._onboarding_lore_text = ""
        for word in text.split():
            self._onboarding_lore_text += word + " "
            self.call_from_thread(self._refresh_dashboard)
            time.sleep(self.TYPEWRITER_WORD_DELAY)

    def _onboarding_ask_next(self) -> None:
        from .onboarding import build_onboarding_prompt_state, required_field_from_setup, required_field_prompt

        session = self._onboarding_session
        if session is None or self._onboarding_reasoner is None:
            return

        snapshot = build_onboarding_prompt_state(session)
        missing_field = required_field_from_setup(snapshot)

        if missing_field is None:
            self.call_from_thread(self._onboarding_run_bible_generation)
            return

        self.call_from_thread(self._set_onboarding_status, f"onboarding: asking {missing_field}")

        attempt_count = sum(1 for a in session.answers if a.question_key == missing_field)
        try:
            question = self._onboarding_reasoner.next_question(
                snapshot, missing_field=missing_field, attempt_count=attempt_count
            ).strip()
        except Exception:
            logger.exception("onboarding question generation failed")
            question = required_field_prompt(missing_field, attempt_count=attempt_count)

        if not question:
            question = required_field_prompt(missing_field, attempt_count=attempt_count)

        self._onboarding_asked_field = missing_field
        self._onboarding_current_question = question
        self.call_from_thread(self._onboarding_show_question, question)

    def _onboarding_show_question(self, question: str) -> None:
        self._append_chat("Narrator", question)
        self.status_mode = "idle"
        self.status_message = "onboarding: waiting for answer"
        self._refresh_dashboard()
        self._enable_player_input()

    def _set_onboarding_status(self, message: str) -> None:
        self.status_mode = "story"
        self.status_message = message
        self._refresh_dashboard()

    def _onboarding_handle_answer(self, answer_text: str) -> None:
        from .onboarding import (
            REQUIRED_ONBOARDING_FIELDS,
            build_onboarding_prompt_state,
            normalize_onboarding_setup,
            required_field_from_setup,
            required_field_prompt,
        )

        session = self._onboarding_session
        if session is None or self._onboarding_reasoner is None or self.story_store is None:
            return

        asked_field = self._onboarding_asked_field or REQUIRED_ONBOARDING_FIELDS[0]
        self.call_from_thread(self._set_onboarding_status, f"onboarding: processing {asked_field}")

        snapshot = build_onboarding_prompt_state(session)
        try:
            extracted = self._onboarding_reasoner.extract_updates(
                snapshot, answer_text=answer_text, asked_field=asked_field
            )
        except Exception:
            logger.exception("onboarding extraction failed")
            extracted = {}

        merged = dict(session.normalized_setup)
        if extracted:
            merged.update(extracted)
        normalized = normalize_onboarding_setup(merged, question_order=REQUIRED_ONBOARDING_FIELDS)
        answer_index = len(session.answers)
        for offset, field in enumerate(REQUIRED_ONBOARDING_FIELDS):
            if field not in extracted:
                continue
            self.story_store.record_onboarding_answer(
                session.id,
                question_key=field,
                question_text=required_field_prompt(field, attempt_count=answer_index + offset),
                answer_index=answer_index + offset,
                raw_answer_text=answer_text,
                normalized_answer={field: extracted[field]},
            )
        if asked_field not in extracted:
            self.story_store.record_onboarding_answer(
                session.id,
                question_key=asked_field,
                question_text=self._onboarding_current_question,
                answer_index=answer_index + len(extracted),
                raw_answer_text=answer_text,
                normalized_answer={},
            )
        self._onboarding_session = self.story_store.update_onboarding_session(
            session.id,
            status="in_progress",
            normalized_setup=normalized,
            question_order=REQUIRED_ONBOARDING_FIELDS,
        )

        missing = required_field_from_setup(build_onboarding_prompt_state(self._onboarding_session))
        if missing is None:
            self.call_from_thread(self._onboarding_run_bible_generation)
        else:
            self._onboarding_ask_next()

    def _onboarding_run_bible_generation(self) -> None:
        self.status_mode = "story"
        self.status_message = "onboarding: building story bible..."
        self._refresh_dashboard()
        input_widget = self.query_one("#player_input", Input)
        input_widget.disabled = True
        self.run_worker(self._onboarding_generate_bible, thread=True)

    def _onboarding_on_story_tool(self, tool_name: str, result: str, acc: Any) -> None:
        """Called from story creation worker on each tool completion."""
        label = {
            "story.write_lore": "onboarding: world lore written",
            "story.add_arc": f"onboarding: arc added ({result})",
            "story.add_npc": f"onboarding: npc added ({result})",
            "story.set_scene": "onboarding: opening scene set",
        }.get(tool_name, f"onboarding: {tool_name}")
        self.call_from_thread(self._set_onboarding_status, label)
        if tool_name == "story.write_lore" and acc.lore:
            self._onboarding_typewrite_lore(acc.lore)
        elif tool_name == "story.add_arc" and acc.arcs:
            arc = acc.arcs[-1]
            line = f"\n[Arc] {arc.get('title', '')}: {arc.get('hook', '')}"
            self._onboarding_lore_text += line
            self.call_from_thread(self._refresh_dashboard)
        elif tool_name == "story.add_npc" and acc.npcs:
            npc = acc.npcs[-1]
            line = f"\n[NPC] {npc.get('name', '')} — {npc.get('description', '')}"
            self._onboarding_lore_text += line
            self.call_from_thread(self._refresh_dashboard)

    def _onboarding_generate_bible(self) -> None:
        from .onboarding import (
            REQUIRED_ONBOARDING_FIELDS,
            build_onboarding_seed_scene,
            normalize_onboarding_setup,
            run_story_creation,
        )

        session = self._onboarding_session
        if session is None or self.story_store is None or self._onboarding_reasoner is None:
            return

        normalized = normalize_onboarding_setup(session.normalized_setup, question_order=REQUIRED_ONBOARDING_FIELDS)

        self.call_from_thread(self._set_onboarding_status, "onboarding: building story world...")
        try:
            bible = run_story_creation(
                normalized,
                self._onboarding_reasoner.model,
                on_tool=self._onboarding_on_story_tool,
            )
        except Exception:
            logger.exception("story creation failed")
            bible = None

        seed_scene = build_onboarding_seed_scene(
            normalized,
            session_id=session.session_id,
            onboarding_id=session.id,
            bible=bible,
        )
        summary_text = (bible.intro_text if bible and bible.intro_text else None) or str(
            seed_scene.get("summary_text") or ""
        )
        completed = self.story_store.complete_onboarding_session(
            session.id,
            normalized_setup=normalized,
            generated_summary_text=summary_text,
            seed_scene=seed_scene,
        )
        logger.info("onboarding complete session_id=%s", completed.session_id)
        self.call_from_thread(self._onboarding_finish, completed.session_id, bible)

    def _onboarding_finish(self, session_id: int, bible: StoryBible | None) -> None:
        self._is_onboarding = False
        self._onboarding_session = None
        self.status_mode = "story"
        self.status_message = "onboarding: starting your story..."
        self._refresh_dashboard()
        intro = bible.intro_text if bible else ""
        self.run_worker(lambda: self._onboarding_boot_engine(session_id, intro), thread=True)

    def _onboarding_boot_engine(self, session_id: int, intro_text: str) -> None:
        from .engine import BaseReActEngine as _Engine

        try:
            self.engine = _Engine(
                self._reasoner,
                story_store=self.story_store,
                session_id=session_id,
            )
        except Exception:
            logger.exception("engine boot after onboarding failed")
            self.call_from_thread(self._show_error, RuntimeError("Failed to start game engine after onboarding"))
            return

        if intro_text:
            self._onboarding_typewrite_chat(intro_text)

        self.call_from_thread(self._onboarding_enter_game)

    def _onboarding_typewrite_chat(self, text: str) -> None:
        """Stream intro text into chat using a Static overlay that updates word-by-word."""
        words = text.split()
        buf = ""
        for i, word in enumerate(words):
            buf += ("" if i == 0 else " ") + word
            self.call_from_thread(self._set_intro_preview, buf)
            time.sleep(self.TYPEWRITER_WORD_DELAY)
        # Commit final complete text as a proper chat line
        self.call_from_thread(self._commit_intro_preview, buf)

    def _set_intro_preview(self, text: str) -> None:
        try:
            desc = self.query_one("#scene_description", Static)
            preview = Text()
            preview.append("INTRO  ", style="bold bright_magenta")
            preview.append(text, style="bright_white italic")
            desc.update(preview)
        except Exception:
            pass

    def _commit_intro_preview(self, text: str) -> None:
        try:
            self._append_chat("Narrator", text)
            self._refresh_dashboard()
        except Exception:
            pass

    def _onboarding_enter_game(self) -> None:
        self._refresh_story_cache()
        self._update_engine_scene_viewport_hint()
        cols, rows = self._llm_viewport_size()
        self._last_redraw_size = (cols, rows)
        self._refresh_dashboard()
        # Auto-trigger first narrator turn so player sees opening scene
        self.is_waiting_for_model = True
        self.status_mode = "story"
        self.status_message = "story: opening scene..."
        self.spinner_index = 0
        self.query_one("#player_input", Input).disabled = True
        self._refresh_dashboard()
        self.run_worker(self._onboarding_first_turn, thread=True)

    def _onboarding_first_turn(self) -> None:
        if self.engine is None:
            self.call_from_thread(self._enable_player_input)
            return
        opening_prompt = "Narrate the opening scene. Describe where I am, who I am, and what I sense around me. Set the mood."
        try:
            self.call_from_thread(self._set_story_waiting_status, "story: opening scene...")
            turn = self.engine.run_turn(opening_prompt)
        except Exception as error:
            logger.exception("opening turn failed")
            self.call_from_thread(self._show_error, error)
            return
        self.call_from_thread(self._finish_onboarding_first_turn, turn)

    def _finish_onboarding_first_turn(self, turn: EngineTurn) -> None:
        self._append_chat("Narrator", turn.reply)
        self._status_turn = self.engine.session.turn if self.engine else 0
        if turn.scene_description:
            self._scene_description = turn.scene_description
        self._refresh_story_cache()
        self.is_waiting_for_model = False
        self.status_mode = "idle"
        self.status_message = "ready"
        self._refresh_dashboard()
        cols, rows = self._llm_viewport_size()
        self._last_redraw_size = (cols, rows)
        self._start_map_stream({"cols": cols, "rows": rows})
        self._enable_player_input()


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


def _has_meaningful_map_content(art: str) -> bool:
    return any(char not in {".", "#", " ", "\n", "\r", "\t"} for char in art)


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


_POI_FRAMES = ("*", "\u00b7", "*", "\u2736")      # * · * ✶
_WATER_FRAMES = ("~", "\u2248", "-", "\u2248")    # ~ ≈ - ≈
_PLAYER_FRAMES = ("@", "@", "@", "\u00a4")        # @ @ @ ¤
_UNKNOWN_FRAMES = ("?", "\u00b7", "?", "\u00b7")  # ? · ? ·


def _animate_map_frame(art: str, frame: int) -> str:
    """Apply per-character animation to a map string based on the current frame index."""
    result = []
    for ch in art:
        if ch == "*":
            result.append(_POI_FRAMES[frame % 4])
        elif ch == "~":
            result.append(_WATER_FRAMES[frame % 4])
        elif ch == "@":
            result.append(_PLAYER_FRAMES[frame % 4])
        elif ch == "?":
            result.append(_UNKNOWN_FRAMES[frame % 2])
        else:
            result.append(ch)
    return "".join(result)


class _MapAnimator:
    """Animates NPC digit markers (1-9) moving on floor tiles, plus static char animation."""

    def __init__(self, base_map: str) -> None:
        import random as _random

        self._rng = _random.Random()
        rows = base_map.splitlines()
        self._grid: list[list[str]] = [list(row) for row in rows]
        self._width = max((len(r) for r in self._grid), default=0)
        # Pad all rows to equal width
        for row in self._grid:
            while len(row) < self._width:
                row.append(" ")
        self._height = len(self._grid)
        # Locate NPC digit positions {digit_char: (row, col)}
        self._npcs: dict[str, tuple[int, int]] = {}
        for r, row in enumerate(self._grid):
            for c, ch in enumerate(row):
                if ch.isdigit() and ch != "0":
                    self._npcs[ch] = (r, c)
        self._frame_cache: list[str] = []

    def _is_walkable(self, r: int, c: int) -> bool:
        if r < 0 or r >= self._height or c < 0 or c >= self._width:
            return False
        ch = self._grid[r][c]
        return ch == "."

    def _step_npcs(self) -> None:
        directions = [(-1, 0), (1, 0), (0, -1), (0, 1)]
        for digit, (r, c) in list(self._npcs.items()):
            self._rng.shuffle(directions)
            for dr, dc in directions:
                nr, nc = r + dr, c + dc
                if self._is_walkable(nr, nc):
                    self._grid[r][c] = "."
                    self._grid[nr][nc] = digit
                    self._npcs[digit] = (nr, nc)
                    break

    def frame(self, frame_index: int) -> str:
        # Grow cache lazily
        while len(self._frame_cache) <= frame_index:
            if len(self._frame_cache) > 0:
                self._step_npcs()
            snapshot = "\n".join("".join(row) for row in self._grid)
            self._frame_cache.append(snapshot)
        base = self._frame_cache[frame_index]
        return _animate_map_frame(base, frame_index)


def _styled_lore_text(lore: str) -> Text:
    text = Text()
    text.append("WORLD LORE\n", style="bold bright_magenta")
    if lore.strip():
        text.append(lore.strip(), style="dim italic")
    else:
        text.append("Shaping your world...", style="dim bright_black")
    return text


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
