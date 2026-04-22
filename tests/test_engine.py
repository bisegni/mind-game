import unittest

from mind_game.engine import (
    BaseReActEngine,
    ReActDecision,
    SubagentTask,
    ToolCall,
)
from mind_game.story_state import StoryEdgeDraft, StoryEntityDraft, StoryStateStore


class ScriptedReasoner:
    def __init__(self, decisions):
        self._decisions = list(decisions)
        self.snapshots = []

    def decide(self, snapshot, tools):
        self.snapshots.append(snapshot)
        if not self._decisions:
            raise AssertionError("reasoner was asked for more decisions than expected")
        return self._decisions.pop(0)


class RecordingSubagentRunner:
    def __init__(self):
        self.tasks = []

    def run(self, task: SubagentTask) -> str:
        self.tasks.append(task)
        return f"{task.role}:{task.task}"


class EngineTests(unittest.TestCase):
    def test_run_turn_dispatches_tools_then_returns_final_reply(self) -> None:
        reasoner = ScriptedReasoner(
            [
                ReActDecision(
                    kind="tool",
                    tool=ToolCall(
                        name="session.write_fact",
                        arguments={"key": "tone", "value": "playful"},
                    ),
                ),
                ReActDecision(
                    kind="tool",
                    tool=ToolCall(
                        name="subagent.delegate",
                        arguments={
                            "role": "summarizer",
                            "task": "compress the current state",
                        },
                    ),
                ),
                ReActDecision(kind="final", content="Ready for the next move."),
            ],
        )
        runner = RecordingSubagentRunner()
        engine = BaseReActEngine(reasoner, subagent_runner=runner)

        turn = engine.run_turn("hello there")

        self.assertEqual(turn.reply, "Ready for the next move.")
        self.assertEqual(engine.session.turn, 1)
        self.assertEqual(engine.session.facts["tone"], "playful")
        self.assertEqual(len(runner.tasks), 1)
        self.assertEqual(runner.tasks[0].role, "summarizer")
        self.assertEqual(runner.tasks[0].task, "compress the current state")
        self.assertEqual(runner.tasks[0].context["player_input"], "hello there")
        self.assertEqual(len(turn.observations), 2)
        self.assertEqual(reasoner.snapshots[1]["observations"][0]["tool"], "session.write_fact")
        self.assertEqual(reasoner.snapshots[2]["observations"][1]["tool"], "subagent.delegate")

    def test_tools_are_exposed_as_named_abstractions(self) -> None:
        reasoner = ScriptedReasoner([ReActDecision(kind="final", content="done")])
        engine = BaseReActEngine(reasoner)

        tool_names = [tool.name for tool in engine.tools]

        self.assertIn("session.read", tool_names)
        self.assertIn("session.write_fact", tool_names)
        self.assertIn("session.add_note", tool_names)
        self.assertIn("subagent.delegate", tool_names)

    def test_run_turn_persists_compact_story_state_when_store_is_configured(self) -> None:
        store = StoryStateStore()
        session_id = store.create_session(seed_scene_id="scene:harbor", current_scene_id="scene:harbor")

        harbor = store.upsert_entity(
            session_id,
            entity_type="location",
            name="Harbor",
            canonical_key="location:harbor",
            properties={"mood": "foggy"},
        )
        light = store.upsert_entity(
            session_id,
            entity_type="item",
            name="Beacon Light",
            canonical_key="item:beacon-light",
            properties={"lit": True},
        )

        seed_prompt_state = store.build_prompt_state(session_id, player_input="start", observations=[])
        store.record_turn(
            session_id,
            turn_number=0,
            player_input="start",
            narrator_output="The harbor disappears into fog.",
            prompt_state=seed_prompt_state,
            facts={"weather": "foggy"},
            notes=["keep watch"],
            entities=[
                StoryEntityDraft(
                    entity_type="location",
                    name="Harbor",
                    canonical_key=harbor.canonical_key,
                    properties={"mood": "foggy"},
                ),
            ],
            edges=[
                StoryEdgeDraft(
                    from_entity_key=harbor.canonical_key,
                    to_entity_key=light.canonical_key,
                    edge_type="tracks",
                ),
            ],
            scene_id="scene:harbor",
        )

        reasoner = ScriptedReasoner(
            [
                ReActDecision(
                    kind="tool",
                    tool=ToolCall(
                        name="session.write_fact",
                        arguments={"key": "signal", "value": "open"},
                    ),
                ),
                ReActDecision(kind="final", content="The fog parts around the beacon light."),
            ],
        )
        engine = BaseReActEngine(reasoner, story_store=store, session_id=session_id)

        turn = engine.run_turn("scan the harbor")

        self.assertEqual(turn.reply, "The fog parts around the beacon light.")
        self.assertEqual(engine.session.turn, 2)
        self.assertEqual(engine.session.facts["weather"], "foggy")
        self.assertEqual(engine.session.facts["signal"], "open")
        self.assertEqual(len(reasoner.snapshots[0]["recent_turns"]), 1)
        self.assertEqual(reasoner.snapshots[0]["summary_text"], "The harbor disappears into fog.")
        self.assertTrue(reasoner.snapshots[0]["graph_focus"]["entity_ids"])

        persisted_session = store.load_session(session_id)
        persisted_snapshot = store.latest_snapshot(session_id)
        persisted_turns = store.list_turns(session_id)
        persisted_events = store.list_events(session_id)

        self.assertEqual(persisted_session.current_turn, 2)
        self.assertEqual(persisted_session.current_summary_id, persisted_snapshot.id)
        self.assertEqual(len(persisted_turns), 2)
        self.assertEqual(persisted_snapshot.summary_text, "The fog parts around the beacon light.")
        self.assertEqual(persisted_snapshot.state["facts"]["signal"], "open")
        self.assertTrue(any(event.event_type == "fact" for event in persisted_events))

    def test_engine_uses_latest_stored_session_when_session_id_is_omitted(self) -> None:
        store = StoryStateStore()
        session_id = store.create_session(current_scene_id="scene:bridge")
        prompt_state = store.build_prompt_state(session_id, player_input="start", observations=[])
        store.record_turn(
            session_id,
            turn_number=0,
            player_input="start",
            narrator_output="A lantern glows at the bridge.",
            prompt_state=prompt_state,
            scene_id="scene:bridge",
        )

        reasoner = ScriptedReasoner([ReActDecision(kind="final", content="resume ok")])
        engine = BaseReActEngine(reasoner, story_store=store)

        turn = engine.run_turn("continue")

        self.assertEqual(turn.reply, "resume ok")
        self.assertEqual(engine.session.turn, 2)
        self.assertEqual(engine.session.notes, [])
        self.assertEqual(reasoner.snapshots[0]["summary_text"], "A lantern glows at the bridge.")
        self.assertEqual(reasoner.snapshots[0]["turn"], 1)

    def test_engine_skips_onboarding_sessions_when_bootstrapping_from_store(self) -> None:
        store = StoryStateStore()
        playable_session_id = store.create_session(seed_scene_id="scene:harbor", current_scene_id="scene:harbor")
        onboarding_session_id = store.create_session(status="onboarding")
        store.create_onboarding_session(onboarding_session_id, question_order=["genre"])

        reasoner = ScriptedReasoner([ReActDecision(kind="final", content="ready")])
        engine = BaseReActEngine(reasoner, story_store=store)

        self.assertEqual(engine.story_session_id, playable_session_id)


if __name__ == "__main__":
    unittest.main()
