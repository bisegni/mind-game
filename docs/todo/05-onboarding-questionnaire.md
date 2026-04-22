# 05. Onboarding Questionnaire Todo

## Goal

Define the onboarding questionnaire as the world-setup flow that starts when no session exists or when the player chooses to create a new session.
This todo builds on the base engine and adds the session-creation world setup flow.

## Trigger

- Start onboarding if there is no active session.
- Start onboarding if the player requests a new session.
- Keep onboarding separate from the normal story loop.

## Onboarding Concept

- The onboarding process should be handled by an agent.
- The agent should ask a small set of structured questions.
- The answers should be used to create the lore, setting, and target of the new story.
- Once onboarding is complete, the game should transition into the actual story engine.

## Questions To Collect

- Story theme or genre
- Tone and mood
- Setting or world style
- Player role or identity
- Main objective or target
- Difficulty or challenge level
- Any key lore constraints or must-have elements

## Data To Store

- Session id
- Onboarding answers
- Generated lore summary
- Story target or campaign goal
- Canonical world facts
- Player preferences
- Seed scene or opening state
- Timestamps

## Storage Plan

- Store the raw onboarding answers in the session.
- Convert the answers into a compact structured setup record.
- Save the generated lore summary separately from the raw answers.
- Keep the setup data small enough for fast prompt loading.
- Make the stored setup easy to reuse when the session resumes.

## Agent Workflow

1. Detect that onboarding is required.
2. Ask structured questions one at a time or in a short guided sequence.
3. Capture the player answers in session storage.
4. Convert the answers into a concise story setup.
5. Build the initial lore, target, and opening scene from that setup.
6. Hand control to the main story agent and the normal game loop.

## Design Rules

- Keep onboarding focused on world setup, not gameplay.
- Do not let onboarding bloat the main story context.
- Use the onboarding results to seed memory, lore, and the first scene.
- Preserve the raw answers, but rely on the compact setup for the live prompt.
- Make the transition from onboarding to game play feel seamless.

## Todo Items

- [ ] Define when onboarding starts and how it is detected.
- [ ] Define the exact questionnaire flow and question order.
- [ ] Define how onboarding answers are stored in SQLite.
- [ ] Define how raw answers become structured lore and target data.
- [ ] Define the initial scene generation from onboarding results.
- [ ] Define the handoff from onboarding agent to story agent.
- [ ] Define how the setup is reloaded for resumed sessions.

## Acceptance Criteria

- A new session starts with an onboarding questionnaire.
- The answers are turned into a usable story setup.
- The story engine receives a compact world seed instead of a long raw questionnaire.
- The player can start the actual game immediately after onboarding completes.
