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
            {"sessions", "turns", "entities", "edges", "state_snapshots", "events"}.issubset(tables),
        )
        self.assertTrue(
            {
                "idx_turns_session_turn",
                "idx_entities_session_type_key",
                "idx_edges_session_from_type",
                "idx_edges_session_to_type",
                "idx_state_snapshots_session_turn",
                "idx_events_session_turn",
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
