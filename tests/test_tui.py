import asyncio
import threading
import unittest
from types import SimpleNamespace

from mind_game.engine import EngineTurn

try:
    from textual.widgets import Input, RichLog, Static

    from mind_game.tui import MindGameApp, render_scene_map
except ModuleNotFoundError:  # pragma: no cover - depends on optional local install
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

        async with app.run_test() as pilot:
            player_input = app.query_one("#player_input", Input)
            player_input.value = "i open the door"
            await pilot.press("enter")
            await self._wait_for_event(started)

            status = app.query_one("#status", Static)
            self.assertIn("st: thinking", str(status.render()))
            self.assertTrue(player_input.disabled)
            self.assertGreaterEqual(engine.scene_viewport_size["cols"], 40)
            self.assertGreaterEqual(engine.scene_viewport_size["rows"], 7)

            first_status = str(status.render())
            await pilot.pause(0.25)
            self.assertNotEqual(first_status, str(status.render()))

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

        async with app.run_test() as pilot:
            player_input = app.query_one("#player_input", Input)
            player_input.value = "go inside"
            await pilot.press("enter")
            await pilot.pause(0.2)

            rendered = str(app.query_one("#situation", Static).render())
            self.assertIn("MAP / SITUATION", rendered)
            self.assertIn("/\\", rendered)
            self.assertIn("||", rendered)
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
        self.assertEqual(rendered_lines[1], "|@" + " " * 9 + "|")
        empty_row = "|" + " " * 10 + "|"
        self.assertEqual(rendered_lines[2], empty_row)

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

        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            engine.redraw_calls.clear()
            app._last_redraw_size = (0, 0)
            app._trigger_scene_redraw()
            await pilot.pause(0.2)

            self.assertEqual(len(engine.redraw_calls), 1)
            viewport = engine.redraw_calls[0]
            self.assertGreaterEqual(viewport["cols"], 40)
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
            rendered = str(app.query_one("#status", Static).render())
        scene_lines = [line for line in rendered.splitlines() if line.startswith("sc:")]

        self.assertEqual(len(scene_lines), 1)
        self.assertLessEqual(len(scene_lines[0]), 21)
        self.assertIn("…", scene_lines[0])
        self.assertIn("mdl:", rendered)
        self.assertNotIn("scene:", rendered)
        self.assertNotIn("model:", rendered)

    async def _wait_for_event(self, event: threading.Event) -> None:
        for _ in range(20):
            if event.is_set():
                return
            await asyncio.sleep(0.05)
        self.fail("engine.run_turn was not called")


if __name__ == "__main__":
    unittest.main()


