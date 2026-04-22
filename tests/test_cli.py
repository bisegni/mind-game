import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from mind_game.engine import ReActDecision

import mind_game.cli as cli


class FakeReasoner:
    def decide(self, snapshot, tools):
        return ReActDecision(kind="final", content=f"echo:{snapshot['player_input']}")


class CliTests(unittest.TestCase):
    def test_main_uses_package_cli_and_engine_loop(self) -> None:
        with patch.dict("os.environ", {"OLLAMA_MODEL": "test-model", "OLLAMA_BASE_URL": "http://example.local:11434"}, clear=True):
            with patch.object(cli, "build_reasoner", return_value=FakeReasoner()) as build_reasoner:
                with patch("builtins.input", side_effect=["hello", "exit"]):
                    stdout = io.StringIO()
                    with redirect_stdout(stdout):
                        exit_code = cli.main()

        output = stdout.getvalue()

        self.assertEqual(exit_code, 0)
        self.assertIn('Mind Game chat loop ready using Ollama model "test-model" at http://example.local:11434.', output)
        self.assertIn("AI  > echo:hello", output)
        build_reasoner.assert_called_once_with("test-model", "http://example.local:11434")

    def test_main_passes_configured_story_store_to_the_engine(self) -> None:
        class FakeEngine:
            def __init__(self, reasoner, story_store=None):
                self.reasoner = reasoner
                self.story_store = story_store

            def run_turn(self, player_input):
                self.last_input = player_input
                return SimpleNamespace(reply=f"echo:{player_input}")

        class FakeStoryStore:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        story_store = FakeStoryStore()

        with patch.dict("os.environ", {"MIND_GAME_STORY_DB_PATH": "/tmp/story.sqlite3"}, clear=True):
            with patch.object(cli, "build_reasoner", return_value=FakeReasoner()):
                with patch.object(cli, "build_story_store", return_value=story_store) as build_story_store:
                    with patch.object(cli, "BaseReActEngine", side_effect=FakeEngine) as engine_cls:
                        with patch("builtins.input", side_effect=["hello", "exit"]):
                            stdout = io.StringIO()
                            with redirect_stdout(stdout):
                                exit_code = cli.main()

        self.assertEqual(exit_code, 0)
        build_story_store.assert_called_once_with()
        engine_cls.assert_called_once()
        self.assertIs(engine_cls.call_args.kwargs["story_store"], story_store)
        self.assertIn("AI  > echo:hello", stdout.getvalue())
        self.assertTrue(story_store.closed)

    def test_reasoner_decide_uses_the_layered_turn_prompt_path(self) -> None:
        class PromptRecordingModel:
            def __init__(self) -> None:
                self.calls = []

            def invoke(self, messages):
                self.calls.append(messages)
                return SimpleNamespace(content='{"kind":"final","content":"ready"}')

        model = PromptRecordingModel()
        reasoner = cli.OllamaReActReasoner(model=model, system_prompt="system prompt")
        snapshot = {
            "turn": 2,
            "player_input": "continue",
            "facts": {},
            "recent_messages": [],
            "notes": [],
            "observations": [],
        }
        tools = [SimpleNamespace(name="session.read", description="Return a compact session snapshot.")]

        with patch.object(cli, "build_turn_prompt", return_value="TURN PROMPT SENTINEL") as build_turn_prompt:
            decision = reasoner.decide(snapshot, tools)

        self.assertEqual(decision, ReActDecision(kind="final", content="ready"))
        build_turn_prompt.assert_called_once_with(snapshot, tools)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(model.calls[0][0].content, "system prompt")
        self.assertIn("bounded ReAct turn for the Mind Game prototype", model.calls[0][1].content)
        self.assertIn("TURN PROMPT SENTINEL", model.calls[0][1].content)


if __name__ == "__main__":
    unittest.main()
