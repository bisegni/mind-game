# 03. Game Mechanics Todo

## Goal

Define the core loop for a red-text, AI-narrated storyline game where the model acts as the narrator and the game state is persisted in SQLite.
This todo builds on the base engine and adds the story-state rules it operates on.

## Mechanic Outline

- The player advances through a text story one turn at a time.
- The AI model is the narrator and describes the world, events, and consequences.
- Each player action changes the story state.
- The game keeps a persistent session so the story can continue across turns and resumes.

## SQLite Memory Plan

- Store each session in SQLite.
- Store each turn as a record linked to the session.
- Keep a compact, structured game state for fast retrieval by the model.
- Preserve enough history to reconstruct the story without loading the entire transcript every time.

## Information To Store Each Turn

- Session id
- Turn number
- Player input
- Narrator output
- Scene/location
- Story summary
- Active characters
- Inventory or resources
- Quests or goals
- Flags and choices already made
- Important world facts
- Timestamps

## Model Memory Workflow

1. Load the session state from SQLite before generating the next turn.
2. Load the most relevant story facts, not the full raw history.
3. Use the stored state to decide the next narration step.
4. Write the new turn back to SQLite.
5. Update the compact state with any new facts, choices, or consequences.

## Design Rules

- Keep the narrator response short enough to feel like a game turn.
- Make state updates explicit after each player action.
- Prefer structured memory over long transcript replay.
- Save only relevant story information so the next step can be computed quickly.
- Use the database as the source of truth for story continuity.

## Todo Items

- [ ] Define the SQLite schema for sessions, turns, and state snapshots.
- [ ] Define which story fields are canonical and always stored.
- [ ] Define the prompt contract for loading memory before each AI turn.
- [ ] Define how summaries are generated from older turns.
- [ ] Define how player choices are saved and reloaded.
- [ ] Define the minimal state needed to calculate the next move.
- [ ] Define the resume flow for continuing an existing session.

## Acceptance Criteria

- A new session can be created and stored in SQLite.
- Each turn can be saved with the relevant story information.
- The AI can reload the current session state quickly before the next narration.
- The game can continue from persisted memory without losing continuity.
