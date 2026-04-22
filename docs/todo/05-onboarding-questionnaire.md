# 05. Onboarding Questionnaire Todo

## Goal

Define the onboarding questionnaire as the world-setup flow that runs when no playable session exists or when the player explicitly asks to start over.
This todo builds on the base engine and creates the session-creation path that turns player preferences into a compact story seed.

## Design Thesis

- Onboarding should capture enough intent to build a strong opening world, but not so much that it becomes a second game loop.
- The questionnaire should produce structured setup data, not just free-form notes.
- The raw answers should remain stored for traceability, while the live prompt should read a compressed setup record.
- The flow should be deterministic enough to resume, but flexible enough to handle vague player answers.
- The transition from onboarding to the main story loop should feel like scene zero, not a separate setup wizard.

## Trigger Conditions

Start onboarding when any of the following is true:

- No active session exists.
- The existing session is marked complete, closed, or abandoned.
- The player selects a "new session" or "restart story" action.
- The current session is missing its seed scene or onboarding record and cannot safely resume.

Do not start onboarding when:

- A valid active session already exists.
- The player is resuming a session that already has a stored world seed.
- The current story can continue from the saved prompt snapshot.

## Onboarding States

Model onboarding as a small state machine so the engine can resume or recover cleanly.

- `pending`: onboarding is needed but has not started.
- `in_progress`: questions have begun and the session is collecting answers.
- `reviewing`: the engine has enough answers and is converting them into a world seed.
- `complete`: the onboarding record has produced the session seed and opening scene.
- `skipped`: the player declined the setup flow and accepted defaults.
- `aborted`: onboarding failed or was interrupted before a usable seed was produced.

## Questionnaire Design

The questionnaire should ask a small number of structured questions, then optionally ask one follow-up if an answer is too vague.
The default flow should stay short enough to finish in one sitting.

### Core questions

- Story theme or genre
- Tone and mood
- Setting or world style
- Player role or identity
- Main objective or campaign goal
- Difficulty or challenge level
- Key lore constraints or must-have elements

### Recommended follow-up prompts

Use follow-ups only when the answer is underspecified:

- "What makes this setting feel different from a standard version of that genre?"
- "Do you want the world to feel grounded, fantastical, eerie, hopeful, or chaotic?"
- "Should the player start as a chosen one, an outsider, a survivor, a detective, or something else?"
- "What should definitely exist, and what should definitely not exist?"

### Answer handling rules

- Accept short answers without forcing long prose.
- Normalize vague responses into a small set of canonical tags.
- Keep the player in control when the model proposes suggestions.
- Allow a "surprise me" path that uses default theme bundles.
- Avoid asking more than one follow-up unless the answer would otherwise block world generation.

## Data To Collect

Capture both raw text and normalized story-building data.

- Session id
- Onboarding record id
- Question key
- Question text
- Raw answer text
- Normalized answer value
- Confidence or specificity level
- Player-selected options, if the answer came from a choice list
- Generated setup summary
- Canonical world facts
- Campaign goal or target
- Player preferences
- Seed scene summary
- Timestamps

## Storage Model

Store onboarding as its own durable record linked to the session.
That keeps the setup flow separate from turn history while still letting the main engine resume from the resulting seed.

### Recommended tables

#### `onboarding_sessions`

- `id`
- `session_id`
- `status`
- `question_order_json`
- `answers_json`
- `normalized_setup_json`
- `generated_summary_text`
- `seed_scene_json`
- `created_at`
- `updated_at`
- `completed_at`

#### `onboarding_answers`

- `id`
- `onboarding_session_id`
- `question_key`
- `question_text`
- `answer_index`
- `raw_answer_text`
- `normalized_answer_json`
- `created_at`

#### `sessions` additions

- `onboarding_id`
- `seed_scene_id`
- `current_scene_id`
- `current_summary_id`
- `status`
- `updated_at`

### Why this shape works

- `onboarding_sessions` keeps the entire setup flow resumable.
- `onboarding_answers` preserves the question-by-question history for debugging and replay.
- `normalized_setup_json` gives the story engine a compact, prompt-friendly seed.
- `seed_scene_json` keeps the opening state close to the setup record that produced it.

## Normalized Setup Shape

Convert raw answers into a compact world seed before handing control to the story engine.

Suggested fields:

- `genre`
- `tone`
- `setting`
- `player_role`
- `campaign_goal`
- `difficulty`
- `must_have_constraints`
- `must_avoid_constraints`
- `world_tags`
- `opening_hook`
- `starting_state`
- `story_promises`
- `memory_seed`

The normalized setup should answer these questions quickly:

- What kind of story is this?
- What should the player feel immediately?
- What should definitely be true in the world?
- What should the first scene be trying to establish?

## Agent Workflow

1. Detect that onboarding is required.
2. Create or load the onboarding session record.
3. Ask the core questions in a short guided sequence.
4. Save each raw answer immediately.
5. Normalize the answers into a compact setup record.
6. Generate the campaign goal, world facts, and seed scene.
7. Mark the onboarding record complete.
8. Hand the resulting seed to the main story engine.

## Handoff Rules

- The onboarding agent owns only the setup flow.
- The main story agent should receive the compact normalized setup, not the full answer history.
- The first playable scene should already reflect the onboarding choices.
- If onboarding ends early, the engine should either retry, apply defaults, or mark the session aborted rather than pretending the seed is complete.
- The handoff should include the session id, onboarding id, seed scene id, and summary snapshot.

## Resume and Recovery

The onboarding flow should be restartable without losing the player’s partial answers.

- If the app closes mid-questionnaire, reload the onboarding session and continue from the next unanswered question.
- If the answers are already complete but the seed scene is missing, regenerate the seed from the normalized setup.
- If the onboarding data is inconsistent, fall back to a safe restart rather than booting a broken story state.
- If the player explicitly restarts, archive the old onboarding record and create a new one.

## Prompt Contract

The onboarding prompt should stay smaller than the normal story prompt.

- Include only the active question, the short answer history, and the minimal setup goals.
- Do not load full story history during onboarding.
- Keep any generated suggestions short and concrete.
- Use the same narrator voice rules as the main engine, but with a setup-oriented tone.
- After completion, stop emitting onboarding-specific context and hand over the compact seed.

## Design Rules

- Keep onboarding focused on world setup, not gameplay.
- Do not let onboarding bloat the main story context.
- Use onboarding results to seed memory, lore, and the first scene.
- Preserve raw answers, but rely on the compact normalized setup for live prompt loading.
- Make the transition from onboarding to gameplay feel seamless.
- Prefer explicit structure over free-form narrative whenever the story seed can be represented as data.

## Todo Items

- [ ] Define the exact trigger logic for when onboarding starts, resumes, or restarts.
- [ ] Define the question order, follow-up policy, and fallback defaults.
- [ ] Define the SQLite schema for onboarding sessions and per-question answers.
- [ ] Define the normalized setup JSON shape used by the story engine.
- [ ] Define how raw answers become canonical world facts and campaign goals.
- [ ] Define the seed scene generation rules for the first playable turn.
- [ ] Define the handoff contract from onboarding agent to story agent.
- [ ] Define the resume and recovery path for interrupted onboarding sessions.

## Acceptance Criteria

- A new session starts with an onboarding questionnaire when no active session exists.
- The questionnaire records both raw answers and normalized setup data.
- The story engine receives a compact world seed instead of a long questionnaire transcript.
- The opening scene reflects the onboarding answers and starts the game immediately.
- Interrupted onboarding can resume without losing progress.
- A broken or incomplete setup does not silently produce an invalid playable session.

## Implementation Notes

- `mind_game/story_state.py` should own the onboarding tables, session linkage, and seed reconstruction helpers.
- `mind_game/engine.py` should branch into onboarding when the session lacks a valid seed.
- `mind_game/cli.py` should let the player restart or resume onboarding without manually editing storage.
- The prompt layer should treat onboarding as a compact setup pack, not a transcript replay.
- The onboarding record should remain small enough to load quickly when a session resumes.
