from __future__ import annotations

import unittest

from mind_game.console import (
    ConsoleMessage,
    load_session_messages,
    render_message,
    render_session_history,
)
from mind_game.story_state import StoryStateStore


class ConsoleTests(unittest.TestCase):
    def test_load_session_messages_reconstructs_chat_pairs_in_order(self) -> None:
        store = StoryStateStore()
        session_id = store.create_session(current_scene_id="scene:harbor")

        first_prompt_state = store.build_prompt_state(session_id, player_input="look out", observations=[])
        store.record_turn(
            session_id,
            turn_number=0,
            player_input="look out",
            narrator_output="The harbor lights flicker in the mist.",
            prompt_state=first_prompt_state,
            scene_id="scene:harbor",
        )

        second_prompt_state = store.build_prompt_state(session_id, player_input="listen", observations=[])
        store.record_turn(
            session_id,
            turn_number=1,
            player_input="listen",
            narrator_output="You hear ropes creak against the dock.",
            prompt_state=second_prompt_state,
            scene_id="scene:harbor",
        )

        messages = load_session_messages(store, session_id)

        self.assertEqual(
            [(message.role, message.turn_number, message.content) for message in messages],
            [
                ("player", 0, "look out"),
                ("narrator", 0, "The harbor lights flicker in the mist."),
                ("player", 1, "listen"),
                ("narrator", 1, "You hear ropes creak against the dock."),
            ],
        )
        self.assertTrue(all(message.scene_id == "scene:harbor" for message in messages))

    def test_render_session_history_renders_chronological_chat_transcript(self) -> None:
        store = StoryStateStore()
        session_id = store.create_session(current_scene_id="scene:harbor")

        first_prompt_state = store.build_prompt_state(session_id, player_input="look out", observations=[])
        store.record_turn(
            session_id,
            turn_number=0,
            player_input="look out",
            narrator_output="The harbor lights flicker in the mist.",
            prompt_state=first_prompt_state,
            scene_id="scene:harbor",
        )

        second_prompt_state = store.build_prompt_state(session_id, player_input="listen", observations=[])
        store.record_turn(
            session_id,
            turn_number=1,
            player_input="listen",
            narrator_output="You hear ropes creak against the dock.",
            prompt_state=second_prompt_state,
            scene_id="scene:harbor",
        )

        history = render_session_history(store, session_id, use_color=False)

        self.assertIn(f"Session {session_id} | 4 messages | started", history)
        self.assertIn("turn 0", history)
        self.assertIn("turn 1", history)
        self.assertIn("Player   | look out", history)
        self.assertIn("Narrator | The harbor lights flicker in the mist.", history)
        self.assertIn("Player   | listen", history)
        self.assertIn("Narrator | You hear ropes creak against the dock.", history)
        self.assertLess(history.index("look out"), history.index("listen"))
        self.assertIn("\n\nturn 1", history)

    def test_render_session_history_uses_distinct_color_theme_for_speakers(self) -> None:
        store = StoryStateStore()
        session_id = store.create_session(current_scene_id="scene:bridge")
        prompt_state = store.build_prompt_state(session_id, player_input="step forward", observations=[])
        store.record_turn(
            session_id,
            turn_number=0,
            player_input="step forward",
            narrator_output="The bridge groans under your weight.",
            prompt_state=prompt_state,
            scene_id="scene:bridge",
        )

        history = render_session_history(store, session_id, use_color=True)

        self.assertIn("\x1b[2;38;5;245mSession", history)
        self.assertIn("\x1b[1;38;5;214mPlayer  \x1b[0m | \x1b[0mstep forward\x1b[0m", history)
        self.assertIn("\x1b[1;38;5;45mNarrator\x1b[0m | \x1b[0mThe bridge groans under your weight.\x1b[0m", history)
        self.assertLess(history.index("Player"), history.index("Narrator"))

    def test_render_message_without_color_keeps_plain_text_labels(self) -> None:
        player_message = render_message(
            ConsoleMessage(
                role="player",
                content="hello",
                turn_number=2,
                created_at="2026-04-22T10:00:00+00:00",
                scene_id="scene:harbor",
            ),
            use_color=False,
        )

        self.assertIn("turn 2", player_message)
        self.assertIn("Player", player_message)
        self.assertIn("hello", player_message)
        self.assertNotIn("\x1b[", player_message)


if __name__ == "__main__":
    unittest.main()
