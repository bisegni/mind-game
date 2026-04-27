import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mind_game.engine import EngineTurn, StreamChunk, TokenUsage
from mind_game.story_state import StoryStateStore

try:
    from textual.app import App as TextualApp
    from textual.css.query import NoMatches
    from textual.widgets import Input, RichLog, Static

    from mind_game.tui import (
        LLM_VIEWPORT_MAX_COLS,
        LLM_VIEWPORT_MAX_RATIO,
        LLM_VIEWPORT_MAX_ROWS,
        LLM_VIEWPORT_MIN_COLS,
        MindGameApp,
        render_scene_map,
    )
except ModuleNotFoundError:  # pragma: no cover - depends on optional local install
    NoMatches = None
    TextualApp = None
    Input = RichLog = Static = None
    MindGameApp = None


@unittest.skipIf(MindGameApp is None, "textual is not installed")
class MindGameAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_runs_engine_and_updates_fixed_panes(self) -> None:
        started = threading.Event()
        release = threading.Event()

        class RecordingEngine:
            story_session_id = None

            def __init__(self) -> None:
                self.session = SimpleNamespace(turn=0)
                self.inputs = []
                self.redraw_calls = []

            def run_turn(self, player_input):
                self.inputs.append(player_input)
                started.set()
                release.wait(2)
                self.session.turn += 1
                return EngineTurn(
                    player_input=player_input,
                    reply="The hatch sighs open.",
                    observations=[],
                    scene_ascii="+----+\n| @? |\n+----+",
                )

            def redraw_scene(self, *, viewport):
                self.redraw_calls.append(viewport)
                return ""

        engine = RecordingEngine()
        app = MindGameApp(engine=engine, story_store=None, model_name="test-model", base_url="http://example.local")

        async with app.run_test(size=(100, 40)) as pilot:
            player_input = app.query_one("#player_input", Input)
            player_input.value = "i open the door"
            await pilot.press("enter")
            await self._wait_for_event(started)

            status = app.query_one("#status", Static)
            self.assertIn("st: story", str(status.render()))
            self.assertIn("nt: story:", str(status.render()))
            self.assertTrue(player_input.disabled)
            self.assertGreaterEqual(engine.scene_viewport_size["cols"], LLM_VIEWPORT_MIN_COLS)
            self.assertGreaterEqual(engine.scene_viewport_size["rows"], 1)
            self.assertLessEqual(
                engine.scene_viewport_size["cols"],
                max(LLM_VIEWPORT_MIN_COLS, engine.scene_viewport_size["rows"] * LLM_VIEWPORT_MAX_RATIO),
            )

            first_status = str(status.render())
            await pilot.pause(0.25)
            self.assertNotEqual(first_status, str(status.render()))
            self.assertIn("story: waiting for narrator", str(status.render()))

            release.set()
            await pilot.pause(0.2)

            situation = app.query_one("#situation", Static)
            chat = app.query_one("#chat", RichLog)
            self.assertEqual(engine.inputs, ["i open the door"])
            rendered = str(situation.render())
            self.assertIn("MAP / SITUATION", rendered)
            self.assertIn("@", rendered)
            self.assertIn("+----+", rendered)
            self.assertNotIn("Map / ", rendered)
            self.assertGreaterEqual(len(chat.lines), 2)
            self.assertIn("st: idle", str(status.render()))
            self.assertFalse(player_input.disabled)

    async def test_sparse_scene_ascii_is_framed_verbatim_in_panel(self) -> None:
        class SparseSceneEngine:
            story_session_id = None

            def __init__(self) -> None:
                self.session = SimpleNamespace(turn=0)
                self.redraw_calls = []

            def run_turn(self, player_input):
                self.session.turn += 1
                return EngineTurn(
                    player_input=player_input,
                    reply="You step into the alien hatch.",
                    observations=[],
                    scene_ascii="/\\\n||",
                )

            def redraw_scene(self, *, viewport):
                self.redraw_calls.append(viewport)
                return ""

        app = MindGameApp(
            engine=SparseSceneEngine(),
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test(size=(100, 40)) as pilot:
            player_input = app.query_one("#player_input", Input)
            player_input.value = "go inside"
            await pilot.press("enter")
            await pilot.pause(0.2)

            rendered = str(app.query_one("#situation", Static).render())
            self.assertIn("MAP / SITUATION", rendered)
            self.assertIn("/\\", rendered)
            self.assertIn("||", rendered)

    async def test_latest_scene_description_is_shown_for_debugging(self) -> None:
        store = StoryStateStore()
        session_id = store.create_session(current_scene_id="scene:chamber")
        prompt_state = store.build_prompt_state(session_id, player_input="enter", observations=[])
        store.record_turn(
            session_id,
            turn_number=0,
            player_input="enter",
            narrator_output="You step into the chamber.",
            prompt_state=prompt_state,
            scene_id="scene:chamber",
            scene_description="Player stands in a chamber. Console north. Device east. Corridor south.",
            scene_ascii="@..",
        )

        class IdleEngine:
            story_session_id = session_id
            session = SimpleNamespace(turn=1)

            def run_turn(self, player_input):
                raise AssertionError("not used")

            def stream_map(self, *, viewport, on_chunk=None):
                return ""

            def redraw_scene(self, *, viewport):
                return ""

        app = MindGameApp(
            engine=IdleEngine(),
            story_store=store,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test(size=(100, 40)):
            rendered = str(app.query_one("#scene_description", Static).render())

            self.assertIn("SCENE DESCRIPTION", rendered)
            self.assertIn("Console north", rendered)
            self.assertLessEqual(rendered.count("\n") + 1, app.query_one("#situation", Static).size.height)
            self.assertNotIn("Map /", rendered)

    def test_render_scene_map_frames_existing_ascii_verbatim(self) -> None:
        dense = "\n".join(
            [
                "@----?",
                "|    |",
                "|*  *|",
                "+----+",
            ],
        )

        rendered = render_scene_map(dense, cols=20, rows=8)
        rendered_lines = rendered.splitlines()

        self.assertEqual(len(rendered_lines), 8)
        self.assertTrue(all(len(line) == 20 for line in rendered_lines))
        self.assertTrue(rendered_lines[0].startswith("+="))
        self.assertTrue(rendered_lines[-1].startswith("+="))
        self.assertIn("@----?", rendered_lines[1])
        self.assertIn("|*  *|", rendered_lines[3])

    def test_render_scene_map_pads_short_ascii_to_target_rows(self) -> None:
        rendered = render_scene_map("@", cols=12, rows=6)
        rendered_lines = rendered.splitlines()

        self.assertEqual(len(rendered_lines), 6)
        self.assertTrue(all(len(line) == 12 for line in rendered_lines))
        self.assertEqual(rendered_lines[0], "+" + "=" * 10 + "+")
        self.assertEqual(rendered_lines[-1], "+" + "=" * 10 + "+")
        self.assertEqual(rendered_lines[1], "|@" + "." * 9 + "|")
        empty_row = "|" + "." * 10 + "|"
        self.assertEqual(rendered_lines[2], empty_row)

    def test_render_scene_map_clips_oversized_ascii_to_target_rows_and_columns(self) -> None:
        oversized = "\n".join(f"row-{index}-" + "x" * 40 for index in range(20))

        rendered = render_scene_map(oversized, cols=16, rows=7)
        rendered_lines = rendered.splitlines()

        self.assertEqual(len(rendered_lines), 7)
        self.assertTrue(all(len(line) == 16 for line in rendered_lines))
        self.assertIn("row-0-", rendered_lines[1])
        self.assertIn("row-4-", rendered_lines[5])
        self.assertNotIn("row-5-", rendered)

    def test_render_scene_map_clears_persisted_wall_fill_rows(self) -> None:
        rendered = render_scene_map("@.......\n########\n########", cols=10, rows=5)
        rendered_lines = rendered.splitlines()

        self.assertEqual(rendered_lines[2], "|" + "." * 8 + "|")
        self.assertEqual(rendered_lines[3], "|" + "." * 8 + "|")

    def test_render_scene_map_clears_repeated_wall_spam_rows(self) -> None:
        rendered = render_scene_map("@...................\n######.#####.#####.\n######.#####.#####.", cols=22, rows=5)
        rendered_lines = rendered.splitlines()

        self.assertEqual(rendered_lines[2], "|" + "." * 20 + "|")
        self.assertEqual(rendered_lines[3], "|" + "." * 20 + "|")

    async def test_resize_schedules_engine_redraw_with_new_viewport(self) -> None:
        class RedrawEngine:
            story_session_id = None

            def __init__(self) -> None:
                self.session = SimpleNamespace(turn=0)
                self.redraw_calls = []

            def run_turn(self, player_input):
                raise AssertionError("not used")

            def redraw_scene(self, *, viewport):
                self.redraw_calls.append(viewport)
                return "+--+\n|@?|\n+--+"

        engine = RedrawEngine()
        app = MindGameApp(engine=engine, story_store=None, model_name="test-model", base_url="http://example.local")

        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.05)
            engine.redraw_calls.clear()
            app._last_redraw_size = (0, 0)
            app._trigger_scene_redraw()
            await pilot.pause(0.2)

            self.assertEqual(len(engine.redraw_calls), 1)
            viewport = engine.redraw_calls[0]
            self.assertGreaterEqual(viewport["cols"], LLM_VIEWPORT_MIN_COLS)
            self.assertGreaterEqual(viewport["rows"], 1)
            rendered = str(app.query_one("#situation", Static).render())
            self.assertIn("@?", rendered)

    async def test_status_text_clips_long_scene_id_to_one_line(self) -> None:
        long_scene = (
            "scene:onboarding:1:a-distant-outpost-that-find-an-alien-spacecraft-"
            "without-anyone-on-board-and-there-are-information-about-the-end"
        )

        class LongSceneStore:
            def load_session(self, session_id):
                return SimpleNamespace(current_turn=9, current_scene_id=long_scene)

            def latest_snapshot(self, session_id):
                return None

            def list_turns(self, session_id, limit=None):
                return []

        class Engine:
            story_session_id = 1
            session = SimpleNamespace(turn=9)

            def run_turn(self, player_input):
                raise AssertionError("not used")

        app = MindGameApp(
            engine=Engine(),
            story_store=LongSceneStore(),
            model_name="gpt-oss:20b",
            base_url="http://example.local",
        )

        async with app.run_test():
            status = app.query_one("#status", Static)
            rendered = str(status.render())
            status_width = status.size.width
        scene_lines = [line for line in rendered.splitlines() if line.startswith("sc:")]

        self.assertEqual(len(scene_lines), 1)
        self.assertLessEqual(len(scene_lines[0]), status_width)
        self.assertIn("…", scene_lines[0])
        self.assertIn("mdl:", rendered)
        self.assertNotIn("scene:", rendered)
        self.assertNotIn("model:", rendered)

    async def test_llm_viewport_caps_aspect_ratio_and_size(self) -> None:
        class IdleEngine:
            story_session_id = None
            session = SimpleNamespace(turn=0)

            def run_turn(self, player_input):
                raise AssertionError("not used")

            def redraw_scene(self, *, viewport):
                return ""

        app = MindGameApp(
            engine=IdleEngine(),
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test(size=(220, 50)):
            cols, rows = app._llm_viewport_size()
            widget_cols, widget_rows = app._widget_viewport_size()

        self.assertLessEqual(rows, LLM_VIEWPORT_MAX_ROWS)
        self.assertLessEqual(cols, LLM_VIEWPORT_MAX_COLS)
        self.assertLessEqual(cols, max(LLM_VIEWPORT_MIN_COLS, rows * LLM_VIEWPORT_MAX_RATIO))
        self.assertLessEqual(cols, widget_cols)
        self.assertLess(rows, widget_rows)

    async def test_layout_stacks_status_above_situation_full_width(self) -> None:
        class IdleEngine:
            story_session_id = None
            session = SimpleNamespace(turn=0)

            def run_turn(self, player_input):
                raise AssertionError("not used")

            def redraw_scene(self, *, viewport):
                return ""

        app = MindGameApp(
            engine=IdleEngine(),
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test(size=(120, 40)):
            status = app.query_one("#status", Static)
            situation = app.query_one("#situation", Static)
            screen_width = app.size.width
            self.assertEqual(status.region.width, screen_width)
            self.assertEqual(situation.region.width, screen_width)
            self.assertLess(status.region.y, situation.region.y)

    async def test_map_streaming_status_is_distinct_and_does_not_disable_input(self) -> None:
        map_started = threading.Event()
        map_release = threading.Event()

        class StreamingEngine:
            story_session_id = None

            def __init__(self) -> None:
                self.session = SimpleNamespace(turn=0)

            def run_turn(self, player_input):
                self.session.turn += 1
                return EngineTurn(
                    player_input=player_input,
                    reply="The corridor brightens.",
                    observations=[],
                    scene_ascii="",
                )

            def stream_map(self, *, viewport, on_chunk=None):
                map_started.set()
                if on_chunk is not None:
                    on_chunk("@..\n###")
                map_release.wait(2)
                return "@..\n###"

            def redraw_scene(self, *, viewport):
                return ""

        app = MindGameApp(
            engine=StreamingEngine(),
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test() as pilot:
            player_input = app.query_one("#player_input", Input)
            player_input.value = "go ahead"
            await pilot.press("enter")
            await self._wait_for_event(map_started)
            await pilot.pause(0.2)

            rendered_status = str(app.query_one("#status", Static).render())
            self.assertIn("st: map", rendered_status)
            self.assertIn("nt: map: streaming", rendered_status)
            self.assertFalse(player_input.disabled)

            map_release.set()
            await pilot.pause(0.2)
            self.assertIn("nt: ready", str(app.query_one("#status", Static).render()))

    async def test_map_stream_finish_applies_final_returned_ascii(self) -> None:
        map_started = threading.Event()

        class FinalFallbackEngine:
            story_session_id = None
            session = SimpleNamespace(turn=0)

            def run_turn(self, player_input):
                raise AssertionError("not used")

            def stream_map(self, *, viewport, on_chunk=None):
                map_started.set()
                if on_chunk is not None:
                    on_chunk("........................\n........................")
                return "..........*CON..........\n.*PAN......@......*DEV.\n............?..........."

            def redraw_scene(self, *, viewport):
                return ""

        app = MindGameApp(
            engine=FinalFallbackEngine(),
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test(size=(80, 30)) as pilot:
            app._start_map_stream({"cols": 24, "rows": 4})
            await self._wait_for_event(map_started)
            await pilot.pause(0.2)

            self.assertIn("*CON", app.current_scene_ascii)
            self.assertIn("*PAN", app.current_scene_ascii)
            self.assertIn("*DEV", app.current_scene_ascii)

    async def test_partial_map_stream_is_clipped_to_requested_viewport(self) -> None:
        class IdleEngine:
            story_session_id = None
            session = SimpleNamespace(turn=0)

            def run_turn(self, player_input):
                raise AssertionError("not used")

            def stream_map(self, *, viewport, on_chunk=None):
                return ""

            def redraw_scene(self, *, viewport):
                return ""

        app = MindGameApp(
            engine=IdleEngine(),
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test():
            app._map_stream_viewport = (6, 3)
            app._apply_partial_scene("abcdefghi\n123456789\nzzzzzzzzz\nextra-line")

            self.assertEqual(app.current_scene_ascii, "abcdef\n123456\nzzzzzz")
            rendered = str(app.query_one("#situation", Static).render())
            self.assertLessEqual(rendered.count("\n") + 1, app.query_one("#situation", Static).size.height)

    async def test_partial_map_stream_status_includes_token_usage_when_available(self) -> None:
        class IdleEngine:
            story_session_id = None
            session = SimpleNamespace(turn=0)

            def run_turn(self, player_input):
                raise AssertionError("not used")

            def stream_map(self, *, viewport, on_chunk=None):
                return ""

            def redraw_scene(self, *, viewport):
                return ""

        app = MindGameApp(
            engine=IdleEngine(),
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test():
            app._map_stream_viewport = (12, 4)
            app._apply_partial_scene(
                StreamChunk(
                    content="@...\n####",
                    usage=TokenUsage(prompt_tokens=12, completion_tokens=38, total_tokens=242),
                ),
            )

            rendered_status = str(app.query_one("#status", Static).render())
            self.assertIn("map: streaming 2/4 tok 38/242", rendered_status)

    async def test_reasoning_only_map_progress_updates_status_without_clearing_map(self) -> None:
        class IdleEngine:
            story_session_id = None
            session = SimpleNamespace(turn=0)

            def run_turn(self, player_input):
                raise AssertionError("not used")

            def stream_map(self, *, viewport, on_chunk=None):
                return ""

            def redraw_scene(self, *, viewport):
                return ""

        app = MindGameApp(
            engine=IdleEngine(),
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test():
            app.is_streaming_map = True
            app._map_stream_viewport = (12, 4)
            app.current_scene_ascii = "@...\n####"
            app._map_stream_line_count = 2
            app._reset_map_timeout()

            app._apply_partial_scene(StreamChunk(content="", usage=TokenUsage(generated_tokens=3)))

            self.assertEqual(app.current_scene_ascii, "@...\n####")
            rendered_status = str(app.query_one("#status", Static).render())
            self.assertIn("map: streaming 2/4 tok 3", rendered_status)

    async def test_stale_map_finish_after_timeout_does_not_clear_error_status(self) -> None:
        class IdleEngine:
            story_session_id = None
            session = SimpleNamespace(turn=0)

            def run_turn(self, player_input):
                raise AssertionError("not used")

            def stream_map(self, *, viewport, on_chunk=None):
                return ""

            def redraw_scene(self, *, viewport):
                return ""

        app = MindGameApp(
            engine=IdleEngine(),
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test():
            app._finish_map_error(TimeoutError("timed out"))
            app._finish_map_stream()

            self.assertIn("map failed: timed out", str(app.query_one("#status", Static).render()))

    async def test_late_partial_map_chunk_after_teardown_is_ignored(self) -> None:
        class IdleEngine:
            story_session_id = None
            session = SimpleNamespace(turn=0)

            def run_turn(self, player_input):
                raise AssertionError("not used")

            def stream_map(self, *, viewport, on_chunk=None):
                return ""

            def redraw_scene(self, *, viewport):
                return ""

        app = MindGameApp(
            engine=IdleEngine(),
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test():
            app.is_streaming_map = True
            app._map_stream_viewport = (12, 4)
            app._refresh_dashboard = Mock(side_effect=NoMatches("missing"))

            app._apply_partial_scene(StreamChunk(content="@...", usage=TokenUsage(generated_tokens=1)))

            self.assertEqual(app.current_scene_ascii, "@...")
            self.assertEqual(app.status_mode, "map")

    async def test_stale_map_error_after_shutdown_is_ignored(self) -> None:
        class IdleEngine:
            story_session_id = None
            session = SimpleNamespace(turn=0)

            def run_turn(self, player_input):
                raise AssertionError("not used")

            def stream_map(self, *, viewport, on_chunk=None):
                return ""

            def redraw_scene(self, *, viewport):
                return ""

        app = MindGameApp(
            engine=IdleEngine(),
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test():
            app.status_mode = "idle"
            app.status_message = "ready"
            app.is_streaming_map = False
            app._map_stream_viewport = None

            app._finish_map_error(NoMatches("missing"))

            self.assertEqual(app.status_mode, "idle")
            self.assertEqual(app.status_message, "ready")

    async def test_map_status_stays_queued_until_worker_enters_backend_call(self) -> None:
        class IdleEngine:
            story_session_id = None
            session = SimpleNamespace(turn=0)

            def __init__(self) -> None:
                self.stream_calls = 0

            def run_turn(self, player_input):
                raise AssertionError("not used")

            def stream_map(self, *, viewport, on_chunk=None):
                self.stream_calls += 1
                return ""

            def redraw_scene(self, *, viewport):
                return ""

        app = MindGameApp(
            engine=IdleEngine(),
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test():
            app._stop_map_stream_status()
            app.engine.stream_calls = 0
            app.run_worker = lambda *args, **kwargs: SimpleNamespace(cancel=lambda: None)
            app._start_map_stream({"cols": 40, "rows": 10})

            rendered_status = str(app.query_one("#status", Static).render())
            self.assertIn("nt: map: queued 40x10", rendered_status)
            self.assertEqual(app.engine.stream_calls, 0)

    async def test_mount_starts_initial_map_stream(self) -> None:
        map_started = threading.Event()

        class StreamingEngine:
            story_session_id = None
            session = SimpleNamespace(turn=0)

            def __init__(self) -> None:
                self.viewports = []

            def run_turn(self, player_input):
                raise AssertionError("not used")

            def stream_map(self, *, viewport, on_chunk=None):
                self.viewports.append(viewport)
                map_started.set()
                if on_chunk is not None:
                    on_chunk("@")
                return "@"

            def redraw_scene(self, *, viewport):
                return ""

        engine = StreamingEngine()
        app = MindGameApp(
            engine=engine,
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test(size=(100, 40)):
            await self._wait_for_event(map_started)

            self.assertEqual(len(engine.viewports), 1)
            self.assertGreaterEqual(engine.scene_viewport_size["cols"], LLM_VIEWPORT_MIN_COLS)
            self.assertGreaterEqual(engine.scene_viewport_size["rows"], 1)

    async def test_player_submit_cancels_visible_map_worker_before_queueing_story(self) -> None:
        cancel_called = threading.Event()

        class IdleEngine:
            story_session_id = None
            session = SimpleNamespace(turn=0)

            def run_turn(self, player_input):
                return EngineTurn(player_input=player_input, reply="ok", observations=[])

            def stream_map(self, *, viewport, on_chunk=None):
                return ""

            def redraw_scene(self, *, viewport):
                return ""

        app = MindGameApp(
            engine=IdleEngine(),
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test() as pilot:
            player_input = app.query_one("#player_input", Input)
            app._stop_map_stream_status()
            app.is_streaming_map = True
            app.status_mode = "map"
            app.status_message = "map: requesting 40x10"
            app._map_worker = SimpleNamespace(cancel=cancel_called.set)
            app.run_worker = lambda *args, **kwargs: SimpleNamespace(cancel=lambda: None)
            app.on_input_submitted(SimpleNamespace(value="go now", input=player_input))

            self.assertTrue(cancel_called.is_set())
            self.assertFalse(app.is_streaming_map)
            self.assertIn("story:", str(app.query_one("#status", Static).render()))

    async def test_exit_cancels_workers_and_pending_map_state(self) -> None:
        cancel_called = threading.Event()
        cancel_all_called = threading.Event()

        class IdleEngine:
            story_session_id = None
            session = SimpleNamespace(turn=0)

            def run_turn(self, player_input):
                raise AssertionError("not used")

            def stream_map(self, *, viewport, on_chunk=None):
                return ""

            def redraw_scene(self, *, viewport):
                return ""

        app = MindGameApp(
            engine=IdleEngine(),
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test():
            app.is_streaming_map = True
            app.is_waiting_for_model = True
            app._pending_map_viewport = {"cols": 40, "rows": 10}
            app._map_worker = SimpleNamespace(cancel=cancel_called.set)
            app.workers.cancel_all = cancel_all_called.set

            app._prepare_for_shutdown()

            self.assertTrue(cancel_called.is_set())
            self.assertTrue(cancel_all_called.is_set())
            self.assertFalse(app.is_streaming_map)
            self.assertFalse(app.is_waiting_for_model)
            self.assertIsNone(app._pending_map_viewport)

    async def test_exit_app_delegates_to_textual_without_event_argument(self) -> None:
        class IdleEngine:
            story_session_id = None
            session = SimpleNamespace(turn=0)

            def run_turn(self, player_input):
                raise AssertionError("not used")

            def stream_map(self, *, viewport, on_chunk=None):
                return ""

            def redraw_scene(self, *, viewport):
                return ""

        app = MindGameApp(
            engine=IdleEngine(),
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test():
            exit_app = Mock(return_value=None)
            with patch.object(TextualApp, "_on_exit_app", new=exit_app):
                app._on_exit_app(SimpleNamespace())

            exit_app.assert_called_once_with()

    async def test_map_timeout_changes_status_and_cancels_worker(self) -> None:
        cancel_called = threading.Event()

        class IdleEngine:
            story_session_id = None
            session = SimpleNamespace(turn=0)

            def run_turn(self, player_input):
                raise AssertionError("not used")

            def stream_map(self, *, viewport, on_chunk=None):
                return ""

            def redraw_scene(self, *, viewport):
                return ""

        app = MindGameApp(
            engine=IdleEngine(),
            story_store=None,
            model_name="test-model",
            base_url="http://example.local",
        )

        async with app.run_test():
            app._stop_map_stream_status()
            app.is_streaming_map = True
            app.status_mode = "map"
            app.status_message = "map: requesting 40x10"
            app._map_worker = SimpleNamespace(cancel=cancel_called.set)

            app._map_stream_timed_out()

            self.assertTrue(cancel_called.is_set())
            self.assertFalse(app.is_streaming_map)
            self.assertIn("map failed: timed out", str(app.query_one("#status", Static).render()))

    async def _wait_for_event(self, event: threading.Event) -> None:
        for _ in range(20):
            if event.is_set():
                return
            await asyncio.sleep(0.05)
        self.fail("engine.run_turn was not called")


if __name__ == "__main__":
    unittest.main()
