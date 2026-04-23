from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mind_game.scene_renderer import render_scene_frame, render_scene_text
from mind_game.story_state import StoryStateStore


class SceneRendererTests(unittest.TestCase):
    def test_render_scene_frame_is_deterministic_for_known_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story-state.sqlite3"
            store = StoryStateStore(path)
            session_id = store.create_session(current_scene_id="scene:harbor")

            prompt_state = store.build_prompt_state(session_id, player_input="look toward the light", observations=[])
            store.record_turn(
                session_id,
                turn_number=0,
                player_input="look toward the light",
                narrator_output="A beacon cuts through the fog.",
                prompt_state=prompt_state,
                facts={"tone": "tense", "setting": "foggy harbor"},
                notes=["stay near the light", "keep quiet"],
                observations=[{"tool": "session.read", "result": '{"turn":0}'}],
                scene_id="scene:harbor",
            )

            snapshot = store.latest_snapshot(session_id)
            self.assertIsNotNone(snapshot)

            frame = render_scene_frame(snapshot, width=48, height=16, use_color=False)
            text = render_scene_text(snapshot, width=48, height=16, use_color=False)

            expected = (
                "+----------------------------------------------+\n"
                "| scene: scene:harbor                          |\n"
                "| summary: A beacon cuts through the fog.      |\n"
                "| facts:                                       |\n"
                "| setting: foggy harbor                        |\n"
                "| tone: tense                                  |\n"
                "| notes:                                       |\n"
                "| - stay near the light                        |\n"
                "| - keep quiet                                 |\n"
                "| observations:                                |\n"
                '| - session.read -> {"turn":0}                 |\n'
                "| focus: entity_ids: 2, 4, 5                   |\n"
                "+----------------------------------------------+"
            )

            self.assertEqual(frame.text, expected)
            self.assertEqual(text, expected)
            self.assertEqual(frame.scene_id, "scene:harbor")

    def test_render_scene_frame_can_toggle_color_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story-state.sqlite3"
            store = StoryStateStore(path)
            session_id = store.create_session(current_scene_id="scene:bridge")

            prompt_state = store.build_prompt_state(session_id, player_input="step forward", observations=[])
            store.record_turn(
                session_id,
                turn_number=0,
                player_input="step forward",
                narrator_output="The bridge groans under your weight.",
                prompt_state=prompt_state,
                facts={"weather": "windy"},
                scene_id="scene:bridge",
            )

            snapshot = store.latest_snapshot(session_id)
            self.assertIsNotNone(snapshot)

            monochrome = render_scene_text(snapshot, width=48, height=16, use_color=False)
            colored = render_scene_text(snapshot, width=48, height=16, use_color=True)

            self.assertNotIn("\x1b[", monochrome)
            self.assertIn("\x1b[", colored)


if __name__ == "__main__":
    unittest.main()
