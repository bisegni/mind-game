import unittest
from types import SimpleNamespace

from mind_game.prompt import (
    COMPACT_MEMORY_LAYER,
    GAME_LOOP_LAYER,
    NARRATOR_VOICE_LAYER,
    PROMPT_ERROR_LAYER,
    TOOL_CONTEXT_LAYER,
    build_system_prompt,
    build_turn_prompt,
    is_exit_command,
    normalize_user_input,
)


class PromptTests(unittest.TestCase):
    def test_build_system_prompt_exposes_the_layered_contract(self) -> None:
        prompt = build_system_prompt()

        self.assertIn(GAME_LOOP_LAYER, prompt)
        self.assertIn(NARRATOR_VOICE_LAYER, prompt)
        self.assertIn(COMPACT_MEMORY_LAYER, prompt)
        self.assertIn(TOOL_CONTEXT_LAYER, prompt)
        self.assertIn(PROMPT_ERROR_LAYER, prompt)
        self.assertIn("one concise question at a time", prompt)
        self.assertIn("tone, setting, challenge level", prompt)

    def test_build_turn_prompt_includes_compact_memory_tool_and_error_guidance(self) -> None:
        snapshot = {
            "turn": 7,
            "player_input": "look around",
            "current_scene_id": "scene:harbor",
            "current_summary_id": 42,
            "summary_text": "A beacon cuts through the fog.",
            "facts": {"tone": "playful"},
            "recent_messages": [
                {"role": "player", "content": "hello"},
                {"role": "assistant", "content": "Welcome back."},
            ],
            "notes": ["keep the reply brief"],
            "graph_focus": {"entity_ids": [11, 12]},
            "entities": [
                {"id": 11, "entity_type": "location", "canonical_key": "location:harbor", "name": "Harbor"},
            ],
            "edges": [
                {"id": 22, "from_entity_id": 11, "to_entity_id": 12, "edge_type": "tracks", "weight": 1.0},
            ],
            "recent_turns": [
                {"id": 1, "turn_number": 0, "player_input": "start", "narrator_output": "Fog rolls in."},
            ],
            "observations": [
                {"tool": "session.read", "result": '{"turn": 7}'},
            ],
        }
        tools = [
            SimpleNamespace(name="session.read", description="Return a compact session snapshot."),
            SimpleNamespace(name="subagent.delegate", description="Delegate a bounded task."),
        ]

        prompt = build_turn_prompt(snapshot, tools)

        self.assertIn(GAME_LOOP_LAYER, prompt)
        self.assertIn(NARRATOR_VOICE_LAYER, prompt)
        self.assertIn(COMPACT_MEMORY_LAYER, prompt)
        self.assertIn("current_scene_id", prompt)
        self.assertIn("summary_text", prompt)
        self.assertIn("Graph memory:", prompt)
        self.assertIn('"entity_type": "location"', prompt)
        self.assertIn('"edge_type": "tracks"', prompt)
        self.assertIn('"turn_number": 0', prompt)
        self.assertIn(TOOL_CONTEXT_LAYER, prompt)
        self.assertIn(PROMPT_ERROR_LAYER, prompt)
        self.assertIn('"current_scene_id": "scene:harbor"', prompt)
        self.assertIn('"summary_text": "A beacon cuts through the fog."', prompt)
        self.assertIn('"facts": {"tone": "playful"}', prompt)
        self.assertIn('"recent_messages": [{"content": "hello", "role": "player"}', prompt)
        self.assertIn('Tool catalog: [{"description": "Return a compact session snapshot.", "name": "session.read"}', prompt)
        self.assertIn('Tool results: [{"result": "{\\"turn\\": 7}", "tool": "session.read"}]', prompt)
        self.assertIn("do not rely on or restate", prompt)

    def test_build_turn_prompt_includes_compact_onboarding_seed_data(self) -> None:
        snapshot = {
            "turn": 0,
            "player_input": "",
            "current_scene_id": "scene:onboarding:12:foggy-harbor",
            "summary_text": "You wake in a fog-soaked harbor town.",
            "facts": {"tone": "tense", "setting": "foggy harbor"},
            "recent_messages": [],
            "notes": ["start with a quiet mystery"],
            "onboarding_seed": {
                "onboarding_id": 12,
                "session_id": 34,
                "scene_id": "scene:onboarding:12:foggy-harbor",
                "summary_text": "You wake in a fog-soaked harbor town.",
                "facts": {"genre": "mystery"},
                "world_tags": ["mystery", "harbor"],
                "story_promises": ["quiet mystery"],
                "starting_state": {"text": "dockside"},
                "memory_seed": {"question_order": ["genre", "tone"]},
            },
        }
        tools = [SimpleNamespace(name="session.read", description="Return a compact session snapshot.")]

        prompt = build_turn_prompt(snapshot, tools)

        self.assertIn('"onboarding_seed": {"facts": {"genre": "mystery"}', prompt)
        self.assertIn('"scene_id": "scene:onboarding:12:foggy-harbor"', prompt)
        self.assertIn('"summary_text": "You wake in a fog-soaked harbor town."', prompt)
        self.assertIn('"memory_seed": {"question_order": ["genre", "tone"]}', prompt)

    def test_normalize_user_input_trims_whitespace(self) -> None:
        self.assertEqual(normalize_user_input("  hello  "), "hello")

    def test_is_exit_command_recognizes_quit_words(self) -> None:
        self.assertTrue(is_exit_command("exit"))
        self.assertTrue(is_exit_command(" quit "))
        self.assertFalse(is_exit_command("hello"))


if __name__ == "__main__":
    unittest.main()
