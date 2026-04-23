# 05. Console ASCII Panel

## Goal

Turn the current single-stream console into a split-pane terminal UI:

- Left pane: compact status rail for what the game is doing right now.
- Right pane: live scene viewport that renders the player’s current world as ASCII art with color.

The left pane should make model activity obvious at a glance, especially:

- timing / turn progress
- model request in progress
- tool calls in flight
- spinner / loading state
- error or blocked state

The right pane should continuously show the current scene as an ASCII image or animated ASCII frame sequence, not just plain transcript text.

## Recommendation

Use **Textual** as the main terminal framework.

Why Textual fits this repo:

- The app is already a Python CLI, so staying in-terminal keeps the first implementation simple.
- Textual gives us a clean left/right layout, docked panels, and reactive updates without building a full custom terminal renderer.
- The current project already has a live game loop and persisted session state, which maps well to Textual’s refresh-driven widget model.

For the scene viewport itself:

- Use a dedicated ASCII frame renderer that returns styled text / ANSI-rich lines.
- If the scene needs true animation or video-style playback, treat that renderer as a frame source rather than baking video logic into the UI layer.
- If we need a specialist ASCII animation engine later, evaluate **Asciimatics** as the fallback option, since it explicitly supports ASCII animation and image-to-ASCII rendering.

This means:

- **Textual** owns layout, input, focus, and the split-pane shell.
- **ASCII renderer** owns frames, colors, and any motion.
- The game engine continues to own state and story progression.

## Why not only Rich

Rich is excellent for styled terminal output and live updates, but it is not a full-screen UI framework by itself.

Use Rich-style rendering when:

- we only need styled text or a lightweight live region
- we want to compose ASCII frames cheaply

Do not rely on Rich alone when:

- the UI needs real multi-pane layout
- the left rail must stay fixed while the right viewport updates independently
- we want keyboard focus, widget state, and future interaction controls

## Why not only Asciimatics

Asciimatics is strong for ASCII animation and image-to-ASCII conversion, but it is more specialized than we need for the whole app shell.

Use Asciimatics when:

- the primary product goal is animated ASCII scenes or terminal movie playback
- the app needs a scene/effect model first and a UI shell second

Do not make Asciimatics the only layer unless we decide the entire app should become a classic terminal animation program.

## Proposed UI Shape

```
┌──────────────────────┬──────────────────────────────────────────────┐
│ Status rail          │ Scene viewport                               │
│                      │                                              │
│ turn 12              │  ~~~ fog drift ~~~                           │
│ model: thinking      │  /\\  lighthouse  /\\                        │
│ tool: loading        │   ||      .-.-.      ||                     │
│ spinner: active      │   ||     /     \\     ||                     │
│ scene: harbor        │                                              │
│ latency: 1.8s        │  colored ASCII / frame sequence               │
└──────────────────────┴──────────────────────────────────────────────┘
```

The left rail should be narrow and stable.
The right viewport should take the remaining width and be allowed to refresh frequently.

## Implementation Todo

### 1. Decide the render pipeline

- Define a small interface for scene rendering, for example a function or class that accepts story state and returns a renderable frame.
- Decide whether the first version is:
  - static ASCII scene snapshots
  - animated ASCII frames
  - both, with static fallback
- Keep this interface separate from `mind_game.story_state` so rendering does not leak into persistence.

### 2. Add a Textual app shell

- Create a Textual application module for the console view.
- Build a two-column layout:
  - left status panel
  - right scene panel
- Add a header/footer only if they support the interaction; keep the first version minimal.
- Make the left panel fixed-width or width-constrained.
- Make the scene panel fill the remaining space and refresh independently.

### 3. Model left-panel status indicators

- Add explicit state for:
  - idle
  - user typing
  - model thinking
  - tool call in progress
  - rendering scene
  - error
- Show one active indicator at a time, plus a small summary line for the current turn.
- Prefer short labels and icon-free ASCII-safe markers so the panel remains readable in plain terminals.

### 4. Build the ASCII scene viewport

- Add a renderer that converts the current scene into:
  - ASCII text
  - ANSI color styles
  - optional animation frames
- Decide the source of truth for the viewport:
  - world-state snapshot
  - scene description
  - precomputed frame sequence
- Preserve aspect ratio as much as possible by using a monospace-friendly character set.
- Make color optional so the app still works in monochrome terminals.

### 5. Wire the viewport to game state

- Use the latest story session data to pick the current scene.
- Render the player’s location / situation as the viewport content.
- Refresh the viewport whenever the story advances or the scene changes.
- Keep the transcript available, but do not let it dominate the screen.

### 6. Add loading and spinner states

- Show a spinner while the model is generating.
- Show a distinct tool-call indicator when the assistant delegates work.
- If scene generation takes time, keep the status rail updating even if the viewport is waiting.

### 7. Keep transcript history available

- Preserve the message history in a scrollable region or toggleable view.
- Do not remove the existing transcript behavior until the new layout is stable.
- Consider a keybinding for switching between:
  - transcript focus
  - scene focus
  - status overview

### 8. Test the UI contract

- Add tests for the new render-state model.
- Add tests that verify:
  - the left rail shows active task indicators
  - the scene renderer returns stable ASCII output for a known snapshot
  - the layout chooses the correct pane widths / placement
- Keep existing CLI and story-state tests passing.

## Acceptance Criteria

- The console clearly shows a left status rail and a right ASCII scene viewport.
- Model/tool activity is visible immediately without reading the transcript.
- The scene panel can render colored ASCII content from a story snapshot.
- The UI still works when color is disabled.
- The current onboarding / story loop remains functional.

## Suggested Build Order

1. Add the new Textual shell and basic split-pane layout.
2. Add the left status rail with placeholder indicators.
3. Add a minimal ASCII scene renderer with static frames.
4. Wire the renderer to real story state.
5. Add spinner / tool-call states.
6. Add tests.
7. Evaluate whether we need Asciimatics for richer animation after the first usable version.

