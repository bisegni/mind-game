# 04. Game Console Todo

## Goal

Define the game console as a chat-oriented display for the narrator and player, with a visible list of exchanged messages and session-backed persistence.
This todo builds on the base engine and defines the player-facing chat surface.

## Console Concept

- The console should feel like a chat transcript, not a generic form.
- Messages should alternate between player input and narrator output.
- The player should be able to review the message history in the current session.
- The console should make it obvious which side produced each message.

## Message History Plan

- Save every message into the session.
- Store the sender role, message text, and turn metadata.
- Keep the chronological message list available for the UI.
- Use the saved messages to render the console history after reload.

## Model Memory Plan

- The model should not receive the full transcript every turn.
- Load only the most relevant recent messages plus a compact session summary.
- Use structured memory to preserve the important story facts.
- Keep the prompt small enough that the next narration step stays fast.

## UI Requirements

- Show narrator and player messages clearly.
- Present the conversation as a scrollable chat log.
- Keep the layout simple and readable.
- Support session continuation without losing message history.

## Data To Store

- Session id
- Message id
- Role: player or narrator
- Message content
- Turn number
- Scene or context tag
- Timestamp

## Todo Items

- [ ] Define the console layout and message rendering rules.
- [ ] Define the session message schema in SQLite.
- [ ] Define how the UI loads the message list for an existing session.
- [ ] Define how much recent history the model should read each turn.
- [ ] Define how summaries and memory snippets are injected into the prompt.
- [ ] Define the scrolling and session-resume behavior.

## Acceptance Criteria

- The console displays a chat-like conversation between player and narrator.
- Messages are stored in the session and can be reloaded later.
- The model uses compact memory instead of the full transcript when generating the next response.
- The UI remains clear even as the message history grows.
