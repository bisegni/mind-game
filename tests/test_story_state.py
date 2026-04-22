from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mind_game.story_state import StoryEdgeDraft, StoryEntityDraft, StoryStateStore


class StoryStateStoreTests(unittest.TestCase):
    def test_schema_creation_defines_sessions_turns_entities_edges_snapshots_and_events(self) -> None:
        store = StoryStateStore()

        tables = {
            row["name"]
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            )
        }
        indexes = {
            row["name"]
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'",
            )
        }

        self.assertTrue(
            {
                "sessions",
                "turns",
                "entities",
                "edges",
                "state_snapshots",
                "events",
                "onboarding_sessions",
                "onboarding_answers",
            }.issubset(tables),
        )
        self.assertTrue(
            {
                "idx_turns_session_turn",
                "idx_entities_session_type_key",
                "idx_edges_session_from_type",
                "idx_edges_session_to_type",
                "idx_state_snapshots_session_turn",
                "idx_events_session_turn",
                "idx_onboarding_sessions_session_updated",
                "idx_onboarding_answers_session_index",
            }.issubset(indexes),
        )

    def test_insert_load_and_file_path_round_trip_story_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story-state.sqlite3"
            store = StoryStateStore(path)
            session_id = store.create_session(seed_scene_id="scene:harbor", current_scene_id="scene:harbor")

            harbor = store.upsert_entity(
                session_id,
                entity_type="location",
                name="Harbor",
                canonical_key="location:harbor",
                properties={"mood": "foggy"},
            )
            beacon = store.upsert_entity(
                session_id,
                entity_type="item",
                name="Beacon",
                canonical_key="item:beacon",
                properties={"lit": True},
            )

            first_prompt_state = store.build_prompt_state(
                session_id,
                player_input="look toward the water",
                observations=[],
            )
            turn = store.record_turn(
                session_id,
                turn_number=0,
                player_input="look toward the water",
                narrator_output="A beacon cuts through the fog.",
                prompt_state=first_prompt_state,
                facts={"weather": "foggy"},
                notes=["watch the shoreline"],
                entities=[
                    StoryEntityDraft(
                        entity_type="location",
                        name="Harbor",
                        canonical_key="location:harbor",
                        properties={"mood": "foggy"},
                    ),
                ],
                edges=[
                    StoryEdgeDraft(
                        from_entity_key=harbor.canonical_key,
                        to_entity_key=beacon.canonical_key,
                        edge_type="tracks",
                    ),
                ],
                consequences=["The light reveals a safe path in the water."],
                observations=[{"tool": "session.read", "result": "{\"turn\":0}"}],
                scene_id="scene:harbor",
            )

            store.close()
            reopened = StoryStateStore(path)

            session = reopened.load_session(session_id)
            snapshot = reopened.load_snapshot(turn.state_snapshot_id)
            turns = reopened.list_turns(session_id)
            events = reopened.list_events(session_id)

            self.assertIsNotNone(session)
            self.assertEqual(session.current_turn, 1)
            self.assertEqual(session.current_scene_id, "scene:harbor")
            self.assertEqual(session.current_summary_id, turn.state_snapshot_id)
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.summary_text, "A beacon cuts through the fog.")
            self.assertEqual(snapshot.state["facts"]["weather"], "foggy")
            self.assertEqual(snapshot.state["notes"], ["watch the shoreline"])
            self.assertEqual(turn.state_snapshot_id, snapshot.id)
            self.assertEqual(turn.prompt_hash, reopened.list_turns(session_id)[0].prompt_hash)
            self.assertEqual(len(turns), 1)
            self.assertGreaterEqual(len(events), 3)
            self.assertIn("entity_ids", snapshot.graph_focus)

    def test_onboarding_records_round_trip_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story-state.sqlite3"
            store = StoryStateStore(path)
            session_id = store.create_session()
            onboarding = store.create_onboarding_session(
                session_id,
                question_order=["genre", "tone", "setting"],
            )
            self.assertEqual(onboarding.status, "in_progress")
            self.assertEqual(store.load_session(session_id).status, "onboarding")

            first_answer = store.record_onboarding_answer(
                onboarding.id,
                question_key="genre",
                question_text="What kind of story should this be?",
                answer_index=0,
                raw_answer_text="fog-soaked mystery",
                normalized_answer={"genre": "fog-soaked mystery"},
            )
            store.record_onboarding_answer(
                onboarding.id,
                question_key="tone",
                question_text="What tone do you want?",
                answer_index=1,
                raw_answer_text="tense but hopeful",
                normalized_answer={"tone": "tense but hopeful"},
            )
            store.record_onboarding_answer(
                onboarding.id,
                question_key="setting",
                question_text="Where does the story begin?",
                answer_index=2,
                raw_answer_text="a flooded harbor town",
                normalized_answer={"setting": "a flooded harbor town"},
            )

            updated = store.update_onboarding_session(
                onboarding.id,
                generated_summary_text="Draft onboarding summary",
            )
            completed = store.complete_onboarding_session(onboarding.id)

            store.close()
            reopened = StoryStateStore(path)

            loaded = reopened.load_onboarding_session(onboarding.id)
            linked = reopened.load_session_onboarding(session_id)
            session = reopened.load_session(session_id)
            prompt_state = reopened.build_prompt_state(session_id, player_input="start", observations=[])

            self.assertIsNotNone(loaded)
            self.assertIsNotNone(linked)
            self.assertIsNotNone(session)
            self.assertEqual(loaded, linked)
            self.assertEqual(loaded.status, "complete")
            self.assertEqual(loaded.question_order, ["genre", "tone", "setting"])
            self.assertEqual([answer.question_key for answer in loaded.answers], ["genre", "tone", "setting"])
            self.assertEqual(first_answer.raw_answer_text, "fog-soaked mystery")
            self.assertEqual(loaded.answers[0].normalized_answer["genre"], "fog-soaked mystery")
            self.assertEqual(loaded.normalized_setup["genre"], "fog-soaked mystery")
            self.assertEqual(loaded.normalized_setup["setting"], "a flooded harbor town")
            self.assertEqual(loaded.generated_summary_text, loaded.seed_scene["summary_text"])
            self.assertTrue(loaded.seed_scene["scene_id"].startswith(f"scene:onboarding:{onboarding.id}:"))
            self.assertIsNotNone(loaded.completed_at)
            self.assertEqual(updated.generated_summary_text, "Draft onboarding summary")
            self.assertEqual(completed.status, "complete")
            self.assertEqual(prompt_state["summary_text"], loaded.seed_scene["summary_text"])
            self.assertEqual(prompt_state["current_scene_id"], loaded.seed_scene["scene_id"])
            self.assertEqual(prompt_state["onboarding_seed"]["scene_id"], loaded.seed_scene["scene_id"])
            self.assertEqual(prompt_state["onboarding_seed"]["summary_text"], loaded.seed_scene["summary_text"])
            self.assertEqual(prompt_state["onboarding_seed"]["facts"]["genre"], "fog-soaked mystery")
            self.assertEqual(session.status, "active")
            self.assertEqual(session.onboarding_id, str(onboarding.id))
            self.assertEqual(session.seed_scene_id, loaded.seed_scene["scene_id"])
            self.assertEqual(session.current_scene_id, loaded.seed_scene["scene_id"])

    def test_prompt_state_compacts_recent_turns_and_graph_neighborhood(self) -> None:
        store = StoryStateStore()
        session_id = store.create_session(current_scene_id="scene:bridge")

        bridge = store.upsert_entity(
            session_id,
            entity_type="location",
            name="Bridge",
            canonical_key="location:bridge",
        )
        lantern = store.upsert_entity(
            session_id,
            entity_type="item",
            name="Lantern",
            canonical_key="item:lantern",
        )

        first_state = store.build_prompt_state(session_id, player_input="step forward", observations=[])
        store.record_turn(
            session_id,
            turn_number=0,
            player_input="step forward",
            narrator_output="The bridge groans under your weight.",
            prompt_state=first_state,
            facts={"weather": "windy"},
            notes=["move slowly"],
            entities=[
                StoryEntityDraft(
                    entity_type="location",
                    name="Bridge",
                    canonical_key=bridge.canonical_key,
                ),
            ],
            edges=[
                StoryEdgeDraft(
                    from_entity_key=bridge.canonical_key,
                    to_entity_key=lantern.canonical_key,
                    edge_type="mentions",
                ),
            ],
            scene_id="scene:bridge",
        )

        second_state = store.build_prompt_state(session_id, player_input="listen closely", observations=[])
        store.record_turn(
            session_id,
            turn_number=1,
            player_input="listen closely",
            narrator_output="A lantern flickers in the wind.",
            prompt_state=second_state,
            facts={"signal": "flicker"},
            notes=["follow the light"],
            entities=[
                StoryEntityDraft(
                    entity_type="item",
                    name="Lantern",
                    canonical_key=lantern.canonical_key,
                ),
            ],
            scene_id="scene:bridge",
        )

        state = store.build_prompt_state(session_id, player_input="continue", recent_turn_limit=1)

        self.assertEqual(state["turn"], 2)
        self.assertEqual(state["summary_text"], "A lantern flickers in the wind.")
        self.assertEqual(len(state["recent_messages"]), 2)
        self.assertEqual(state["facts"]["signal"], "flicker")
        self.assertTrue(state["graph_focus"]["entity_ids"])
        self.assertTrue(
            any(entity["canonical_key"] == "item:lantern" for entity in state["entities"]),
        )

    def test_record_turn_rolls_back_partial_writes_when_an_insert_fails(self) -> None:
        store = StoryStateStore()
        session_id = store.create_session(current_scene_id="scene:bridge")
        prompt_state = store.build_prompt_state(session_id, player_input="step forward", observations=[])

        with patch.object(store, "add_edge", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                store.record_turn(
                    session_id,
                    turn_number=0,
                    player_input="step forward",
                    narrator_output="The bridge creaks.",
                    prompt_state=prompt_state,
                    facts={"weather": "windy"},
                    notes=["move slowly"],
                )

        self.assertEqual(len(store.list_turns(session_id)), 0)
        self.assertEqual(
            [entity.canonical_key for entity in store.list_entities(session_id, canonical_keys=["turn:0"])],
            [],
        )
        self.assertIsNone(store.latest_snapshot(session_id))


if __name__ == "__main__":
    unittest.main()
