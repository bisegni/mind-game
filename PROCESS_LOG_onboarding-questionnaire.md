## Plan

| # | Group | Step | Agent | Status |
|---|-------|------|-------|--------|
| 1 | seq | Add SQLite onboarding persistence for raw answers, normalized setup, seed-scene data, and story-session linkage in the existing state layer. | WORKER | ✅ done |
| 2 | seq | Wire onboarding detection and resume into `mind_game/engine.py` and `mind_game/cli.py` so interrupted onboarding reloads stored progress and continues cleanly. | WORKER | ✅ done |
| 3 | seq | Hand off the completed onboarding result as a compact playable session seed so the story engine starts from generated setup data instead of the raw questionnaire transcript. | WORKER | ✅ done |
| 4 | seq | Review the onboarding diff for correctness, edge cases, and test gaps before verification begins. | REVIEWER | ✅ go |
| 5 | seq | Add focused tests for onboarding create/resume/complete flows and run the targeted pytest checks that verify SQLite persistence plus session handoff. | TESTER | ✅ done |
| 6 | seq | Recover orphan `status='onboarding'` sessions on restart by reusing the existing session and creating/loading the onboarding row before any playable-session fallback. | WORKER | ✅ done |

- 2026-04-22 00:00 **[PLANNER]** Plan written — 4 steps (0 parallel groups)
- 2026-04-22 14:37 **[WORKER]** Step 1 ✅ — `mind_game/story_state.py` gained onboarding tables/methods and `tests/test_story_state.py` now covers SQLite onboarding round-trips — current local timestamp `2026-04-22 14:37`
- 2026-04-22 14:44 **[WORKER]** Step 2 ✅ — `mind_game/cli.py` now resumes or starts onboarding before the story loop, and `mind_game/engine.py` only boots from playable active sessions — current local timestamp `2026-04-22 14:44`
- 2026-04-22 14:44 **[WORKER]** Step 3 ✅ — `mind_game/story_state.py` and `mind_game/prompt.py` now surface a compact onboarding seed into the first playable prompt snapshot — current local timestamp `2026-04-22 14:44`
- 2026-04-22 14:49 **[WORKER]** Step 6 ✅ — `mind_game/story_state.py`, `mind_game/cli.py`, and `tests/test_cli.py` now recover orphan onboarding sessions by linking a missing onboarding row to the existing `status='onboarding'` session before session fallback — current local timestamp `2026-04-22 14:49`
- 2026-04-22 14:52 **[REVIEWER]** Step 4 ✅ — go: orphan onboarding sessions are recovered in-place and the added tests cover the bootstrap/resume/complete paths — current local timestamp `2026-04-22 14:52`
- 2026-04-22 14:52 **[TESTER]** Step 5 ✅ — `python -m pytest -q tests/test_story_state.py tests/test_prompt.py tests/test_engine.py tests/test_cli.py` passed (22 passed in 0.11s) — current local timestamp `2026-04-22 14:52`
