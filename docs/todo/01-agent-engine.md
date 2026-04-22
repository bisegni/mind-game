# 01. Agent Engine Todo

## Goal

Define the base game engine as an agent-managed runtime that uses a fast ReAct-style loop, with internal APIs exposed as tools and all orchestration handled by the agent and subagents.

## Engine Concept

- The engine should not rely on a hardcoded imperative controller.
- The main agent should drive the game loop using a ReAct pattern.
- Internal game APIs should be surfaced as tools the agent can call.
- Subagents should handle specialized tasks when the main agent delegates work.
- Orchestration should stay inside the agent workflow instead of a separate manual runner.
- This engine is the foundation that the other todos will extend.

## Runtime Plan

- Use a fast agent framework that supports tool calling and iterative reasoning.
- Run the engine as a loop of think, act, observe, and respond.
- Let the agent decide when to call tools for state, memory, narration, or persistence.
- Keep the control flow simple enough for quick turn response times.

## Tooling Plan

- Expose session access as tools.
- Expose story state reads and writes as tools.
- Expose memory retrieval and summary updates as tools.
- Expose console/message operations as tools.
- Expose any game-specific actions as tools instead of direct function calls.

## Subagent Plan

- Use subagents for focused jobs such as summarization, memory compression, or state validation.
- Keep the main agent responsible for the final game decision.
- Pass only the minimum context needed to each subagent.
- Merge subagent output back into the shared session state.

## Design Rules

- Prefer agent-managed orchestration over application-managed branching.
- Keep tool outputs structured and small.
- Make every important internal capability reachable through the agent tool layer.
- Avoid stuffing the full story context into the prompt when tools can fetch it on demand.
- Use session memory to keep the loop fast and consistent.

## Todo Items

- [ ] Choose the fast ReAct-capable agent framework.
- [ ] Define the main agent loop and turn lifecycle.
- [ ] List all internal APIs that must become tools.
- [ ] Define the tool contract for session, memory, and story state access.
- [ ] Define the subagent roles and delegation boundaries.
- [ ] Define how tool results are merged back into the session.
- [ ] Define the error and retry behavior for failed tool calls.
- [ ] Define how the engine stays fast as the story grows.

## Acceptance Criteria

- The engine can run through an agent-driven ReAct loop.
- Internal APIs are available as tools rather than direct orchestration code.
- The main agent can delegate bounded tasks to subagents.
- The runtime can advance the game while keeping context small and manageable.
