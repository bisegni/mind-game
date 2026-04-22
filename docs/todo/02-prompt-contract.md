# 02. Prompt Contract Todo

## Goal

Define the prompt contract that keeps the agent, narrator, and memory system aligned while the game runs.
This todo builds on the base engine and refines how the agent thinks and speaks.

## Prompt Layers

- System prompt: defines the agent role, game rules, and tool-use expectations.
- Narrator prompt: defines the tone, pacing, and style of the story output.
- Memory prompt: injects compact session facts, recent messages, and relevant summaries.
- Tool context: exposes only the internal information needed for the next action.

## Contract Requirements

- The model should know it is running a story game engine.
- The model should use memory to avoid repeating the full transcript.
- The model should respond as the narrator when producing story output.
- The model should keep responses concise enough for a turn-based chat.
- The model should ask for missing information only when the session state cannot resolve it.

## Memory Injection Plan

- Load the current session summary before each turn.
- Load the last relevant messages instead of the entire chat history.
- Load scene, goals, flags, inventory, and other compact state fields.
- Pass any active tool results into the prompt in a structured format.

## Output Rules

- Narrator output should stay in character.
- Output should be easy to store back into the session.
- If the agent uses tools, the final response should reflect the result of those tools.
- Avoid exposing hidden orchestration details to the player.

## Prompt Safety Rules

- Do not stuff the prompt with redundant history.
- Do not include raw transcripts when summaries are enough.
- Do not let the narrator drift away from the current scene state.
- Keep the contract stable so prompt changes do not break the engine.

## Todo Items

- [ ] Define the system prompt for the agent-managed game loop.
- [ ] Define the narrator style and response constraints.
- [ ] Define the memory summary format injected each turn.
- [ ] Define the recent-message window used for context.
- [ ] Define the tool-result format passed back into the prompt.
- [ ] Define how prompt errors or missing memory are handled.

## Acceptance Criteria

- The prompt contract keeps the narrator consistent across turns.
- The model can use compact memory instead of full history.
- The prompt format is stable enough for agent and subagent orchestration.
- The output can be stored and replayed cleanly in the session.
