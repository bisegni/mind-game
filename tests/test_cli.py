import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType
from unittest.mock import patch

from mind_game.engine import ReActDecision
from mind_game.onboarding import OnboardingDecision
from mind_game.story_state import StoryStateStore

import mind_game.cli as cli


class FakeReasoner:
    def decide(self, snapshot, tools):
        return ReActDecision(kind="final", content=f"echo:{snapshot['player_input']}")


class FakeOnboardingReasoner:
    def __init__(self) -> None:
        self.calls = []

    def decide(self, snapshot):
        self.calls.append(snapshot)
        if len(snapshot.get("recent_answers", [])) == 0:
            return OnboardingDecision(
                kind="question",
                content="What kind of story do you want to play?",
                updates={"genre": "mystery"},
            )

        return OnboardingDecision(
            kind="complete",
            content="We have enough to begin.",
            setup={
                "genre": "mystery",
                "tone": "tense",
                "setting": "foggy harbor",
                "player_role": "a stubborn investigator",
                "campaign_goal": "find the missing ship",
                "difficulty": "moderate",
                "opening_hook": "A fog-choked harbor hides a missing ship and a secret.",
            },
        )


class CoreTriadOnboardingReasoner:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, snapshot):
        self.calls += 1
        if self.calls == 1:
            return OnboardingDecision(
                kind="question",
                content="What genre would you like for your story?",
                updates={"genre": "sci-fi"},
            )
        if self.calls == 2:
            return OnboardingDecision(
                kind="question",
                content="What tone should it have?",
                updates={"tone": "mysterious"},
            )
        if self.calls == 3:
            return OnboardingDecision(
                kind="question",
                content="Where does it begin?",
                updates={"setting": "a space station at the edge of the universe"},
            )
        return OnboardingDecision(kind="question", content="What happens next?", updates={})


class CliTests(unittest.TestCase):
    def test_main_uses_package_cli_and_engine_loop(self) -> None:
        with patch.dict("os.environ", {"OLLAMA_MODEL": "test-model", "OLLAMA_BASE_URL": "http://example.local:11434"}, clear=True):
            with patch.object(cli, "build_reasoner", return_value=FakeReasoner()) as build_reasoner:
                with patch.object(cli, "build_onboarding_reasoner", return_value=FakeOnboardingReasoner()):
                    with patch.object(cli, "build_story_store", return_value=None):
                        with patch("builtins.input", side_effect=["hello", "exit"]):
                            stdout = io.StringIO()
                            with redirect_stdout(stdout):
                                exit_code = cli.main()

        output = stdout.getvalue()

        self.assertEqual(exit_code, 0)
        self.assertIn('Mind Game chat loop ready using Ollama model "test-model" at http://example.local:11434.', output)
        self.assertIn("Player", output)
        self.assertIn("| hello", output)
        self.assertIn("Narrator", output)
        self.assertIn("| echo:hello", output)
        build_reasoner.assert_called_once_with("test-model", "http://example.local:11434")

    def test_main_uses_default_story_store_when_no_db_path_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.sqlite3"
            store = StoryStateStore(path)

            with patch.dict("os.environ", {"OLLAMA_MODEL": "test-model", "OLLAMA_BASE_URL": "http://example.local:11434"}, clear=True):
                with patch.object(cli, "build_reasoner", return_value=FakeReasoner()):
                    with patch.object(cli, "build_onboarding_reasoner", return_value=FakeOnboardingReasoner()):
                        with patch.object(cli, "StoryStateStore", return_value=store) as story_store_cls:
                            with patch("builtins.input", side_effect=["mystery", "hello", "exit"]):
                                stdout = io.StringIO()
                                with redirect_stdout(stdout):
                                    exit_code = cli.main()

            output = stdout.getvalue()
            reopened = StoryStateStore(path)
            session = reopened.latest_playable_session()
            onboarding = reopened.load_session_onboarding(session.id)

            self.assertEqual(exit_code, 0)
            story_store_cls.assert_called_once_with()
            self.assertIn("What kind of story do you want to play?", output)
            self.assertEqual(onboarding.status, "complete")
            self.assertEqual(session.status, "active")
            self.assertEqual([answer.question_key for answer in onboarding.answers], ["exchange_1"])
            self.assertEqual(session.current_scene_id, onboarding.seed_scene["scene_id"])

    def test_main_passes_configured_story_store_to_the_engine(self) -> None:
        class FakeEngine:
            def __init__(self, reasoner, story_store=None, session_id=None):
                self.reasoner = reasoner
                self.story_store = story_store
                self.session_id = session_id
                self.story_session_id = None

            def run_turn(self, player_input):
                self.last_input = player_input
                return SimpleNamespace(reply=f"echo:{player_input}")

        class FakeStoryStore:
            def __init__(self) -> None:
                self.closed = False

            def latest_incomplete_onboarding_session(self):
                return None

            def list_sessions(self, *, statuses=None, limit=None):
                return []

            def latest_session(self, *, status=None):
                return None

            def latest_playable_session(self):
                return SimpleNamespace(
                    id=1,
                    status="active",
                    seed_scene_id="scene:harbor",
                    current_scene_id="scene:harbor",
                    current_turn=0,
                    current_summary_id=None,
                    onboarding_id=None,
                )

            def list_turns(self, session_id, limit=None):
                return []

            def close(self) -> None:
                self.closed = True

        story_store = FakeStoryStore()

        with patch.dict("os.environ", {"MIND_GAME_STORY_DB_PATH": "/tmp/story.sqlite3"}, clear=True):
            with patch.object(cli, "build_reasoner", return_value=FakeReasoner()):
                with patch.object(cli, "build_onboarding_reasoner", return_value=FakeOnboardingReasoner()):
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
        self.assertIn("Player", stdout.getvalue())
        self.assertIn("| hello", stdout.getvalue())
        self.assertIn("Narrator", stdout.getvalue())
        self.assertIn("| echo:hello", stdout.getvalue())
        self.assertTrue(story_store.closed)

    def test_main_starts_onboarding_when_no_playable_session_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.sqlite3"
            store = StoryStateStore(path)

            with patch.dict("os.environ", {"OLLAMA_MODEL": "test-model", "OLLAMA_BASE_URL": "http://example.local:11434"}, clear=True):
                with patch.object(cli, "build_reasoner", return_value=FakeReasoner()):
                    with patch.object(cli, "build_onboarding_reasoner", return_value=FakeOnboardingReasoner()):
                        with patch.object(cli, "build_story_store", return_value=store):
                            with patch("builtins.input", side_effect=["", "mystery", "hello", "exit"]):
                                stdout = io.StringIO()
                                with redirect_stdout(stdout):
                                    exit_code = cli.main()

            output = stdout.getvalue()
            reopened = StoryStateStore(path)
            session = reopened.latest_playable_session()
            onboarding = reopened.load_session_onboarding(session.id)
            turns = reopened.list_turns(session.id)

            self.assertEqual(exit_code, 0)
            self.assertIn("What kind of story do you want to play?", output)
            self.assertIn("A fog-choked harbor hides a missing ship and a secret.", output)
            self.assertIn("| hello", output)
            self.assertEqual(session.status, "active")
            self.assertEqual(session.current_scene_id, onboarding.seed_scene["scene_id"])
            self.assertEqual(onboarding.status, "complete")
            self.assertEqual([answer.question_key for answer in onboarding.answers], ["exchange_1"])
            self.assertEqual([turn.player_input for turn in turns], ["hello"])
            self.assertEqual(session.current_turn, 1)

    def test_main_recovers_orphan_onboarding_session_before_creating_a_new_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.sqlite3"
            store = StoryStateStore(path)
            session_id = store.create_session(status="onboarding")

            with patch.dict("os.environ", {"OLLAMA_MODEL": "test-model", "OLLAMA_BASE_URL": "http://example.local:11434"}, clear=True):
                with patch.object(cli, "build_reasoner", return_value=FakeReasoner()):
                    with patch.object(cli, "build_onboarding_reasoner", return_value=FakeOnboardingReasoner()):
                        with patch.object(cli, "build_story_store", return_value=store):
                            with patch("builtins.input", side_effect=["", "mystery", "hello", "exit"]):
                                stdout = io.StringIO()
                                with redirect_stdout(stdout):
                                    exit_code = cli.main()

            output = stdout.getvalue()
            reopened = StoryStateStore(path)
            session = reopened.load_session(session_id)
            onboarding = reopened.load_session_onboarding(session_id)
            session_count = reopened.connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

            self.assertEqual(exit_code, 0)
            self.assertIn("What kind of story do you want to play?", output)
            self.assertIn("| hello", output)
            self.assertEqual(session_count, 1)
            self.assertIsNotNone(onboarding)
            self.assertEqual(onboarding.status, "complete")
            self.assertEqual(session.status, "active")
            self.assertEqual(session.id, session_id)
            self.assertEqual(session.onboarding_id, str(onboarding.id))
            self.assertEqual(session.current_scene_id, onboarding.seed_scene["scene_id"])
            self.assertEqual([answer.question_key for answer in onboarding.answers], ["exchange_1"])
            self.assertEqual([turn.player_input for turn in reopened.list_turns(session_id)], ["hello"])

    def test_main_resumes_onboarding_from_next_unanswered_question(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.sqlite3"
            store = StoryStateStore(path)
            session_id = store.create_session(status="onboarding")
            onboarding = store.create_onboarding_session(
                session_id,
                status="in_progress",
            )
            store.update_onboarding_session(
                onboarding.id,
                normalized_setup={"genre": "mystery"},
            )

            with patch.dict("os.environ", {"OLLAMA_MODEL": "test-model", "OLLAMA_BASE_URL": "http://example.local:11434"}, clear=True):
                with patch.object(cli, "build_reasoner", return_value=FakeReasoner()):
                    with patch.object(cli, "build_onboarding_reasoner", return_value=FakeOnboardingReasoner()):
                        with patch.object(cli, "build_story_store", return_value=store):
                            with patch("builtins.input", side_effect=["", "mystery", "hello", "exit"]):
                                stdout = io.StringIO()
                                with redirect_stdout(stdout):
                                    exit_code = cli.main()

            output = stdout.getvalue()
            reopened = StoryStateStore(path)
            completed = reopened.load_onboarding_session(onboarding.id)
            session = reopened.load_session(session_id)

            self.assertEqual(exit_code, 0)
            self.assertIn("What kind of story do you want to play?", output)
            self.assertEqual([answer.question_key for answer in completed.answers], ["exchange_1"])
            self.assertEqual(completed.status, "complete")
            self.assertEqual(session.status, "active")
            self.assertEqual(session.current_turn, 1)

    def test_onboarding_auto_completes_after_core_story_fields_are_known(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.sqlite3"
            store = StoryStateStore(path)

            with patch.dict("os.environ", {"OLLAMA_MODEL": "test-model", "OLLAMA_BASE_URL": "http://example.local:11434"}, clear=True):
                with patch.object(cli, "build_reasoner", return_value=FakeReasoner()):
                    with patch.object(cli, "build_onboarding_reasoner", return_value=CoreTriadOnboardingReasoner()):
                        with patch.object(cli, "build_story_store", return_value=store):
                            with patch("builtins.input", side_effect=["mystery", "mysterious", "hello", "exit"]):
                                stdout = io.StringIO()
                                with redirect_stdout(stdout):
                                    exit_code = cli.main()

            output = stdout.getvalue()
            reopened = StoryStateStore(path)
            session = reopened.latest_playable_session()
            onboarding = reopened.load_session_onboarding(session.id)
            turns = reopened.list_turns(session.id)

            self.assertEqual(exit_code, 0)
            self.assertNotIn("goal", output.lower())
            self.assertNotIn("opening hook", output.lower())
            self.assertEqual(onboarding.status, "complete")
            self.assertEqual(session.status, "active")
            self.assertEqual([answer.question_key for answer in onboarding.answers], ["exchange_1", "exchange_2"])
            self.assertEqual([turn.player_input for turn in turns], ["hello"])
            self.assertIn("universe", onboarding.seed_scene["summary_text"].lower())

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

        messages_module = ModuleType("langchain_core.messages")

        class FakeSystemMessage:
            def __init__(self, content):
                self.content = content

        class FakeHumanMessage:
            def __init__(self, content):
                self.content = content

        messages_module.SystemMessage = FakeSystemMessage
        messages_module.HumanMessage = FakeHumanMessage

        langchain_core_module = ModuleType("langchain_core")
        langchain_core_module.messages = messages_module

        with patch.dict(
            sys.modules,
            {
                "langchain_core": langchain_core_module,
                "langchain_core.messages": messages_module,
            },
        ):
            with patch.object(cli, "build_turn_prompt", return_value="TURN PROMPT SENTINEL") as build_turn_prompt:
                decision = reasoner.decide(snapshot, tools)

        self.assertEqual(decision, ReActDecision(kind="final", content="ready"))
        build_turn_prompt.assert_called_once_with(snapshot, tools)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(model.calls[0][0].content, "system prompt")
        self.assertIn("bounded ReAct turn for the Mind Game prototype", model.calls[0][1].content)
        self.assertIn("TURN PROMPT SENTINEL", model.calls[0][1].content)

    def test_onboarding_reasoner_returns_plain_text_when_model_emits_structured_content(self) -> None:
        class PromptRecordingModel:
            def __init__(self) -> None:
                self.calls = []

            def invoke(self, messages):
                self.calls.append(messages)
                return SimpleNamespace(
                    content='{"kind":"question","content":{"genre":null,"story_promises":["A thought-provoking narrative experience."]},"updates":{"genre":"sci-fi"}}',
                )

        model = PromptRecordingModel()
        reasoner = cli.OllamaOnboardingReasoner(model=model, system_prompt="system prompt")
        snapshot = {
            "normalized_setup": {},
            "recent_answers": [],
            "answer_count": 0,
            "coverage": {
                "starting_fields": {
                    "genre": False,
                    "tone": False,
                    "setting": True,
                },
            },
            "generated_summary_text": "",
            "seed_scene": {},
        }

        messages_module = ModuleType("langchain_core.messages")

        class FakeSystemMessage:
            def __init__(self, content):
                self.content = content

        class FakeHumanMessage:
            def __init__(self, content):
                self.content = content

        messages_module.SystemMessage = FakeSystemMessage
        messages_module.HumanMessage = FakeHumanMessage

        langchain_core_module = ModuleType("langchain_core")
        langchain_core_module.messages = messages_module

        with patch.dict(
            sys.modules,
            {
                "langchain_core": langchain_core_module,
                "langchain_core.messages": messages_module,
            },
        ):
            decision = reasoner.decide(snapshot)

        self.assertEqual(decision.kind, "question")
        self.assertNotIn("{", decision.content)
        self.assertTrue(decision.content.lower().startswith("tell me a little more"))

    def test_main_renders_existing_session_history_before_accepting_new_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.sqlite3"
            store = StoryStateStore(path)
            session_id = store.create_session(current_scene_id="scene:harbor")
            seed_state = store.build_prompt_state(session_id, player_input="look around", observations=[])
            store.record_turn(
                session_id,
                turn_number=0,
                player_input="look around",
                narrator_output="The harbor lights glow through the mist.",
                prompt_state=seed_state,
                scene_id="scene:harbor",
            )

            with patch.dict("os.environ", {"OLLAMA_MODEL": "test-model", "OLLAMA_BASE_URL": "http://example.local:11434"}, clear=True):
                with patch.object(cli, "build_reasoner", return_value=FakeReasoner()):
                    with patch.object(cli, "build_onboarding_reasoner", return_value=FakeOnboardingReasoner()):
                        with patch.object(cli, "build_story_store", return_value=store):
                            with patch("builtins.input", side_effect=["hello", "exit"]):
                                stdout = io.StringIO()
                                with redirect_stdout(stdout):
                                    exit_code = cli.main()

            output = stdout.getvalue()
            reopened = StoryStateStore(path)

            self.assertEqual(exit_code, 0)
            self.assertIn("Session", output)
            self.assertIn("look around", output)
            self.assertIn("The harbor lights glow through the mist.", output)
            self.assertIn("Player", output)
            self.assertIn("Narrator", output)
            self.assertIn("echo:hello", output)
            self.assertLess(output.index("look around"), output.index("hello"))

            turns = reopened.list_turns(session_id)
            self.assertEqual(len(turns), 2)
            self.assertEqual([turn.turn_number for turn in turns], [1, 0])
            self.assertEqual([turn.player_input for turn in turns], ["hello", "look around"])
            self.assertEqual([turn.narrator_output for turn in turns], ["echo:hello", "The harbor lights glow through the mist."])


if __name__ == "__main__":
    unittest.main()
