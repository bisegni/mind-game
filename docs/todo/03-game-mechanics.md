# 03. Game Mechanics Todo

## Goal

Define the core loop for a red-text, AI-narrated storyline game where the model acts as the narrator and the game state persists in SQLite.
This todo builds on the base engine and turns the story state into a compact, retrieval-friendly model that the LLM can manage turn by turn.

## Design Thesis

- The game loop works best when the database stores canonical state, not just a transcript.
- The LLM should read a small, structured view of the world on each turn, then write back only the consequences it creates.
- The game graph should live as a queryable projection over SQLite so the engine can retrieve relevant facts, dependencies, and active story threads without loading the whole history.
- The prompt should receive a turn-sized state pack, not raw rows or a long transcript dump.
- The database should support both replay and reasoning: replay for the player, reasoning for the model.

## Current Model Inputs

The current engine snapshot already exposes the kinds of fields the story loop needs:

- `turn`
- `player_input`
- `facts`
- `recent_messages`
- `notes`
- `observations`
- `tool_catalog`

That shape is a useful baseline for the story database because it already separates compact memory from transient observations.

## SQLite Storage Model

Use SQLite as the source of truth for the story session, but model it as layered data instead of a single wide session row.

### Canonical tables

- `sessions`: one row per story session, with metadata, active status, seed information, and resume pointers.
- `turns`: one row per turn, with player input, narrator output, turn order, and the prompt state used to generate that turn.
- `entities`: one row per persistent world entity, such as characters, locations, items, factions, quests, or facts.
- `edges`: one row per relationship between entities or between a turn and an entity.
- `state_snapshots`: compact world-state checkpoints that represent what the model should read at the start of a turn.
- `events`: optional append-only event log for irreversible actions, discoveries, and consequences.

### Why this shape works

- `sessions` gives fast session lookup and resume control.
- `turns` preserves the playable narrative history.
- `entities` and `edges` form the graph that the model can query for relevance.
- `state_snapshots` provide the exact prompt payload the model should see.
- `events` preserve the causal chain without forcing the prompt to replay every event.

## Graph Model

The graph should be a lightweight knowledge graph, not a separate graph database.
In SQLite, it can be represented with entity rows and edge rows, plus indexes for the common traversal paths.

### Entity types

- `session`
- `turn`
- `scene`
- `character`
- `location`
- `item`
- `quest`
- `goal`
- `flag`
- `fact`
- `choice`
- `consequence`
- `summary`
- `clue`

### Edge types

- `contains`
- `mentions`
- `appears_in`
- `located_in`
- `owns`
- `knows`
- `tracks`
- `advances`
- `unlocks`
- `blocks`
- `resolves`
- `contradicts`
- `summarizes`

### Graph rules

- Every turn links to the scene state it consumed and the consequences it produced.
- Every persistent fact links to the turn that introduced it.
- Every active goal links to the facts, characters, or scenes that can advance it.
- Every summary links back to the turns it compresses.
- A graph edge always carries direction and a relation type so retrieval can follow causal and narrative paths.

## LLM Retrieval Strategy

The model should receive the smallest state bundle that still lets it act coherently.

### Prompt payload per turn

- Current session id
- Current turn number
- Current scene/location
- Current objective or quest
- Active characters
- Important facts and flags
- Inventory/resources
- Recent narrative summary
- Last few meaningful turns
- Open choices and unresolved threads
- Tool results for the current turn

### Retrieval order

1. Load the current state snapshot.
2. Load the active scene and its connected entities.
3. Load unresolved goals, flags, and consequences.
4. Load recent turns only when they add new context.
5. Load older turns only through summaries or graph traversals.

### What the model should not get

- The full raw transcript by default
- Repeated copies of stable facts
- Unfiltered historical turns that do not affect the current choice
- Large JSON blobs that are not directly actionable

## State Compaction Strategy

The story loop needs two kinds of persistence:

- Durable facts that stay true until the story changes them.
- Working memory that only matters for the next few turns.

### Durable state

- Session identity
- Story seed and onboarding answers
- Canonical world facts
- Active quest or campaign goal
- Persistent character roster
- Inventory and resources that matter long term
- Flags with long-lived consequences
- Saved summaries and checkpoints

### Working state

- Recent turn window
- Current player intention
- Open narrative threads
- Temporary tool outputs
- Pending choices
- Local scene modifiers

### Compaction rule

When a turn closes, move stable information into canonical facts or summaries, and discard only the redundant working context.
The prompt should read the compacted result, not the raw turn stream.

## Workflow for LLM-Managed Turns

1. Load the session snapshot and the active graph neighborhood.
2. Build a compact prompt state from the current scene, goals, facts, and recent consequences.
3. Ask the model to narrate the next story beat or request a tool action.
4. Persist the player action, narrator response, and any structured consequences.
5. Update the graph with new facts, relationships, and unresolved threads.
6. Recompute the summary and prompt snapshot for the next turn.

## Prompt Contract For Mechanics

- The narrator prompt should stay in character and keep the response short enough for a turn.
- The memory prompt should load the compact state, not the full transcript.
- The tool context should include only the structured data needed for the next decision.
- Missing or contradictory state should trigger a clarification step instead of hallucinated continuity.
- The prompt should always have a stable fallback when a summary or state segment is missing.

## Recommended Table Fields

### sessions

- `id`
- `created_at`
- `updated_at`
- `status`
- `seed_scene_id`
- `current_turn`
- `current_scene_id`
- `current_summary_id`
- `onboarding_id`

### turns

- `id`
- `session_id`
- `turn_number`
- `player_input`
- `narrator_output`
- `state_snapshot_id`
- `created_at`
- `prompt_hash`

### entities

- `id`
- `session_id`
- `entity_type`
- `name`
- `canonical_key`
- `properties_json`
- `status`
- `created_at`
- `updated_at`

### edges

- `id`
- `session_id`
- `from_entity_id`
- `to_entity_id`
- `edge_type`
- `weight`
- `turn_id`
- `properties_json`

### state_snapshots

- `id`
- `session_id`
- `turn_id`
- `scene_id`
- `summary_text`
- `state_json`
- `graph_focus_json`
- `created_at`

### events

- `id`
- `session_id`
- `turn_id`
- `event_type`
- `payload_json`
- `created_at`

## Index Strategy

The database needs indexes that support story retrieval, not generic reporting.

- Index `turns(session_id, turn_number)` for replay and resume.
- Index `entities(session_id, entity_type, canonical_key)` for fast lookup of the active world model.
- Index `edges(session_id, from_entity_id, edge_type)` and `edges(session_id, to_entity_id, edge_type)` for graph traversal.
- Index `state_snapshots(session_id, turn_id)` for prompt assembly.
- Index `events(session_id, turn_id)` for causal history.

## Summary Strategy

Older turns should collapse into summaries that keep the story coherent without preserving every line.

- Generate a turn summary after each meaningful action.
- Roll short summaries into scene summaries.
- Roll scene summaries into session summaries when the story advances.
- Keep the summary text compact, factual, and retrievable.
- Store the summary together with the IDs of the turns it compresses so the model can recover detail when needed.

## Resume Strategy

When a session resumes:

1. Load the latest session snapshot.
2. Load the active scene graph neighborhood.
3. Load the most recent meaningful turns.
4. Load the current summary and unresolved threads.
5. Rebuild the prompt state from the compact snapshot, not from the full transcript.

## Todo Items

- [ ] Define the SQLite schema for sessions, turns, entities, edges, snapshots, and events.
- [ ] Define the canonical world-state fields that always persist.
- [ ] Define the graph projection for scenes, characters, items, quests, and flags.
- [ ] Define the prompt snapshot format fed into the LLM each turn.
- [ ] Define the summary compaction rules for older turns and scene transitions.
- [ ] Define the resume flow for loading the active graph neighborhood and state snapshot.
- [ ] Define the write-back path for narrator output, player choices, and consequences.

## Acceptance Criteria

- A new session can be created and stored in SQLite.
- Each turn can be saved with the relevant story information and a compact prompt snapshot.
- The model can reload the current session state quickly before the next narration.
- The graph projection can answer which facts, entities, and goals are relevant to the next turn.
- The game can continue from persisted memory without losing continuity.
- The prompt receives compact state instead of a full transcript replay.

## Implementation Notes

- `mind_game/story_state.py` owns the SQLite schema, graph projection helpers, snapshot compaction, and persistence APIs.
- `mind_game/engine.py` can run against an in-memory session or a `StoryStateStore`, and it resumes the latest stored session when no explicit session id is provided.
- `mind_game/cli.py` can opt into SQLite persistence with `MIND_GAME_STORY_DB_PATH`, while the default CLI path still works without a database file.
- The prompt layer already consumes the compact state bundle from the engine snapshot, so the mechanics workflow only needs to keep that bundle current and retrieval-friendly.
