import unittest

from mind_game.engine import (
    BaseReActEngine,
    GameMessage,
    ReActDecision,
    StreamChunk,
    SubagentTask,
    TokenUsage,
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

    def test_invalid_tool_call_becomes_observation_instead_of_crashing_turn(self) -> None:
        reasoner = ScriptedReasoner(
            [
                ReActDecision(
                    kind="tool",
                    tool=ToolCall(name="session.write_fact", arguments={"key": "", "value": "ignored"}),
                ),
                ReActDecision(kind="final", content="I will continue without storing that fact."),
            ],
        )
        engine = BaseReActEngine(reasoner)

        turn = engine.run_turn("continue")

        self.assertEqual(turn.reply, "I will continue without storing that fact.")
        self.assertEqual(len(turn.observations), 1)
        self.assertEqual(turn.observations[0].tool, "session.write_fact")
        self.assertIn("tool_error:", turn.observations[0].result)
        self.assertIn("non-empty key", turn.observations[0].result)
        self.assertIn("tool_error:", reasoner.snapshots[1]["observations"][0]["result"])

    def test_tools_are_exposed_as_named_abstractions(self) -> None:
        reasoner = ScriptedReasoner([ReActDecision(kind="final", content="done")])
        engine = BaseReActEngine(reasoner)

        tool_names = [tool.name for tool in engine.tools]

        self.assertIn("session.read", tool_names)
        self.assertIn("session.write_fact", tool_names)
        self.assertIn("session.add_note", tool_names)
        self.assertIn("subagent.delegate", tool_names)

    def test_run_turn_includes_scene_viewport_hint_when_present(self) -> None:
        reasoner = ScriptedReasoner([ReActDecision(kind="final", content="done")])
        engine = BaseReActEngine(reasoner)
        engine.scene_viewport_size = {"cols": 88, "rows": 20}

        engine.run_turn("look around")

        self.assertEqual(reasoner.snapshots[0]["scene_viewport"], {"cols": 88, "rows": 20})

    def test_redraw_scene_returns_scene_ascii_without_advancing_state(self) -> None:
        reasoner = ScriptedReasoner(
            [ReActDecision(kind="final", content="", scene_ascii="+--+\n|@?|\n+--+")],
        )
        engine = BaseReActEngine(reasoner)

        ascii_art = engine.redraw_scene(viewport={"cols": 64, "rows": 12})

        self.assertEqual(ascii_art, "+--+\n|@?|\n+--+")
        self.assertEqual(engine.session.turn, 0)
        self.assertEqual(reasoner.snapshots[0]["scene_viewport"], {"cols": 64, "rows": 12})
        self.assertTrue(reasoner.snapshots[0]["redraw_only"])

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
                ReActDecision(
                    kind="final",
                    content="The fog parts around the beacon light.",
                    scene_description="Player stands on the harbor path. Beacon light is north. Fog surrounds the dock.",
                    scene_ascii="/\\ beacon /\\\n~~ fog ~~",
                ),
            ],
        )
        engine = BaseReActEngine(reasoner, story_store=store, session_id=session_id)

        turn = engine.run_turn("scan the harbor")

        self.assertEqual(turn.reply, "The fog parts around the beacon light.")
        self.assertEqual(
            turn.scene_description,
            "Player stands on the harbor path. Beacon light is north. Fog surrounds the dock.",
        )
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
        self.assertEqual(
            persisted_snapshot.state["scene_description"],
            "Player stands on the harbor path. Beacon light is north. Fog surrounds the dock.",
        )
        self.assertEqual(persisted_snapshot.state["scene_ascii"], "/\\ beacon /\\\n~~ fog ~~")
        self.assertEqual(
            store.build_prompt_state(session_id, player_input="continue")["scene_description"],
            "Player stands on the harbor path. Beacon light is north. Fog surrounds the dock.",
        )
        self.assertEqual(
            store.build_prompt_state(session_id, player_input="continue")["scene_ascii"],
            "/\\ beacon /\\\n~~ fog ~~",
        )
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


    def test_stream_map_invokes_callback_with_growing_buffer(self) -> None:
        chunks = ["+--", "+\n|@", "|\n+--+"]

        class StreamingReasoner(ScriptedReasoner):
            def stream_map(self, snapshot, viewport):
                return iter(chunks)

        reasoner = StreamingReasoner([])
        engine = BaseReActEngine(reasoner)

        received: list[str] = []
        final = engine.stream_map(viewport={"cols": 40, "rows": 8}, on_chunk=received.append)

        self.assertEqual(
            final,
            "+--+" + "." * 36 + "\n"
            "|@|" + "." * 37 + "\n"
            "+--+" + "." * 36 + "\n"
            + "\n".join("." * 40 for _ in range(5)),
        )
        self.assertEqual(received[0], "+--" + "." * 37)
        self.assertEqual(received[1], "+--+" + "." * 36 + "\n|@" + "." * 38)
        self.assertEqual(received[2].splitlines()[2], "+--+" + "." * 36)

    def test_stream_map_logs_scene_description_context(self) -> None:
        store = StoryStateStore()
        session_id = store.create_session(current_scene_id="scene:chamber")
        prompt_state = store.build_prompt_state(session_id, player_input="enter", observations=[])
        store.record_turn(
            session_id,
            turn_number=0,
            player_input="enter",
            narrator_output="You enter the chamber.",
            prompt_state=prompt_state,
            scene_id="scene:chamber",
            scene_description="Player stands in a chamber. Console north. Corridor south.",
        )

        class StreamingReasoner:
            def stream_map(self, snapshot, viewport):
                return iter(["@.."])

        engine = BaseReActEngine(StreamingReasoner(), story_store=store, session_id=session_id)

        with self.assertLogs("mind_game.engine", level="INFO") as logs:
            engine.stream_map(viewport={"cols": 12, "rows": 4})

        output = "\n".join(logs.output)
        self.assertIn("map context viewport=12x4", output)
        self.assertIn("scene_description_present=True", output)
        self.assertIn('scene_description="Player stands in a chamber. Console north. Corridor south."', output)

    def test_stream_map_logs_clipped_one_line_scene_description_context(self) -> None:
        long_description = "Line one\n" + ("very-long " * 80)

        class StreamingReasoner(ScriptedReasoner):
            def stream_map(self, snapshot, viewport):
                self.snapshots.append(snapshot)
                return iter(["@.."])

        reasoner = StreamingReasoner([])
        engine = BaseReActEngine(reasoner)
        engine.session.transcript.append(GameMessage(role="assistant", content="Earlier narration."))

        original_snapshot = engine._snapshot

        def snapshot_with_description(context, observations):
            snapshot = original_snapshot(context, observations)
            snapshot["scene_description"] = long_description
            snapshot["summary_text"] = "summary"
            return snapshot

        engine._snapshot = snapshot_with_description

        with self.assertLogs("mind_game.engine", level="INFO") as logs:
            engine.stream_map(viewport={"cols": 10, "rows": 3})

        output = "\n".join(logs.output)
        self.assertIn("Line one very-long", output)
        self.assertNotIn("\nvery-long", output)
        self.assertIn("...", output)
        logged_description = output.split('scene_description="', 1)[1].split('" summary_chars=', 1)[0]
        self.assertLessEqual(len(logged_description), 500)

    def test_stream_map_passes_usage_chunks_to_callback(self) -> None:
        usage = TokenUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8)

        class StreamingReasoner(ScriptedReasoner):
            def stream_map(self, snapshot, viewport):
                return iter([StreamChunk(content="+--", usage=usage), StreamChunk(content="+")])

        engine = BaseReActEngine(StreamingReasoner([]))
        received: list[object] = []

        final = engine.stream_map(viewport={"cols": 40, "rows": 8}, on_chunk=received.append)

        self.assertEqual(final, "+--+" + "." * 36 + "\n" + "\n".join("." * 40 for _ in range(7)))
        self.assertIsInstance(received[0], StreamChunk)
        self.assertEqual(received[0].content, "+--" + "." * 37)
        self.assertEqual(received[0].usage, usage)
        self.assertEqual(received[1], "+--+" + "." * 36)

    def test_stream_map_stops_after_row_count_and_pads_short_lines(self) -> None:
        yielded = []
        chunks = [
            "abc\n12\n",
            "xyz\n",
            "SHOULD_NOT_BE_READ",
        ]

        class StreamingReasoner(ScriptedReasoner):
            def stream_map(self, snapshot, viewport):
                for chunk in chunks:
                    yielded.append(chunk)
                    yield chunk

        engine = BaseReActEngine(StreamingReasoner([]))
        received: list[str] = []

        final = engine.stream_map(viewport={"cols": 6, "rows": 3}, on_chunk=received.append)

        self.assertEqual(final, "abc...\n12....\nxyz...")
        self.assertEqual(yielded, chunks[:2])
        self.assertEqual(received[-1], "abc...\n12....\nxyz...")

    def test_stream_map_clips_long_lines(self) -> None:
        class StreamingReasoner(ScriptedReasoner):
            def stream_map(self, snapshot, viewport):
                return iter(["abcdefghi\n123456789"])

        engine = BaseReActEngine(StreamingReasoner([]))

        final = engine.stream_map(viewport={"cols": 4, "rows": 2})

        self.assertEqual(final, "abcd\n1234")

    def test_stream_map_replaces_wall_fill_rows_with_empty_floor(self) -> None:
        class StreamingReasoner(ScriptedReasoner):
            def stream_map(self, snapshot, viewport):
                return iter(["@.......\n########\n########"])

        engine = BaseReActEngine(StreamingReasoner([]))

        final = engine.stream_map(viewport={"cols": 8, "rows": 3})

        self.assertEqual(final, "@.......\n........\n........")

    def test_stream_map_replaces_repeated_wall_spam_with_scene_fallback(self) -> None:
        class StreamingReasoner(ScriptedReasoner):
            def stream_map(self, snapshot, viewport):
                return iter(
                    [
                        "@.......................\n"
                        "######.#####.#####.####\n"
                        "######.#####.#####.####\n"
                        "######.#####.#####.####\n"
                        "........................\n"
                        "........................"
                    ],
                )

        reasoner = StreamingReasoner([])
        engine = BaseReActEngine(reasoner)
        original_snapshot = engine._snapshot

        def snapshot_with_description(context, observations):
            snapshot = original_snapshot(context, observations)
            snapshot["scene_description"] = (
                "Player stands in a chamber. Console on far wall. "
                "Control panel left. Humming device right. Corridor behind."
            )
            return snapshot

        engine._snapshot = snapshot_with_description

        final = engine.stream_map(viewport={"cols": 24, "rows": 6})

        self.assertIn("@", final)
        self.assertIn("*PAN", final)
        self.assertIn("*DEV", final)
        self.assertIn("*CON", final)
        self.assertIn("?", final)
        self.assertNotIn("######.#####", final)

    def test_stream_map_stops_at_raw_budget_without_newlines(self) -> None:
        yielded = []

        class StreamingReasoner(ScriptedReasoner):
            def stream_map(self, snapshot, viewport):
                for chunk in ["x" * 200 for _ in range(10)]:
                    yielded.append(chunk)
                    yield chunk

        engine = BaseReActEngine(StreamingReasoner([]))

        final = engine.stream_map(viewport={"cols": 10, "rows": 3})

        self.assertEqual(final, "xxxxxxxxxx\n..........\n..........")
        self.assertEqual(len(yielded), 6)

    def test_stream_map_stops_at_generated_token_budget(self) -> None:
        yielded = []

        class StreamingReasoner(ScriptedReasoner):
            def stream_map(self, snapshot, viewport):
                for index in range(1, 20):
                    yielded.append(index)
                    yield StreamChunk(content="", usage=TokenUsage(generated_tokens=index * 40))

        engine = BaseReActEngine(StreamingReasoner([]))

        final = engine.stream_map(viewport={"cols": 10, "rows": 3})

        self.assertEqual(final, "..........\n..........\n..........")
        self.assertEqual(yielded, [1, 2, 3, 4])

    def test_stream_map_persists_final_ascii_into_latest_snapshot(self) -> None:
        store = StoryStateStore()
        session_id = store.create_session(current_scene_id="scene:vault")
        prompt_state = store.build_prompt_state(session_id, player_input="start", observations=[])
        store.record_turn(
            session_id,
            turn_number=0,
            player_input="start",
            narrator_output="The vault door seals shut.",
            prompt_state=prompt_state,
            scene_id="scene:vault",
        )

        class StreamingReasoner:
            def stream_map(self, snapshot, viewport):
                return iter(["+VAULT+\n|@.....|\n+------+"])

        engine = BaseReActEngine(StreamingReasoner(), story_store=store, session_id=session_id)
        engine.stream_map(viewport={"cols": 10, "rows": 4})

        snapshot = store.latest_snapshot(session_id)
        self.assertEqual(snapshot.state["scene_ascii"], "+VAULT+...\n|@.....|..\n+------+..\n..........")

    def test_stream_map_returns_empty_when_reasoner_lacks_stream_map(self) -> None:
        reasoner = ScriptedReasoner([])
        engine = BaseReActEngine(reasoner)

        result = engine.stream_map(viewport={"cols": 40, "rows": 8})

        self.assertEqual(result, "")
