from __future__ import annotations

import re
import unittest

from mind_game.scene_renderer import render_scene_frame
from mind_game.shell import SceneFrame, ShellStatus, render_scene_viewport, render_split_pane, render_status_rail


class ShellTests(unittest.TestCase):
    def _strip_ansi(self, text: str) -> str:
        return re.sub(r"\x1b\[[0-9;]*m", "", text)

    def _assert_no_partial_ansi(self, text: str) -> None:
        index = 0
        while index < len(text):
            if text[index] == "\x1b" and index + 1 < len(text) and text[index + 1] == "[":
                terminator = text.find("m", index + 2)
                self.assertNotEqual(
                    terminator,
                    -1,
                    msg=f"partial ANSI escape sequence starting at index {index}",
                )
                index = terminator + 1
                continue
            index += 1

    def test_render_status_rail_exposes_activity_states(self) -> None:
        cases = [
            (ShellStatus(mode="idle", turn_number=0, scene_id="scene:harbor"), "state: idle"),
            (ShellStatus(mode="thinking", turn_number=3, scene_id="scene:bridge"), "state: thinking"),
            (ShellStatus(mode="tool_call", tool_name="session.read"), "tool: session.read"),
            (ShellStatus(mode="spinner", message="loading scene", spinner_index=1), "load: / loading scene"),
            (ShellStatus(mode="error", error="database locked"), "error: database locked"),
        ]

        for status, expected in cases:
            with self.subTest(mode=status.mode):
                rail = render_status_rail(status, width=28, height=6, use_color=False)
                self.assertEqual(len(rail), 6)
                self.assertTrue(all(len(line) == 28 for line in rail))
                self.assertIn(expected, "\n".join(rail))

    def test_render_scene_viewport_is_deterministic_for_known_frame(self) -> None:
        frame = SceneFrame(
            title="Harbor",
            subtitle="low fog",
            lines=(
                "~ ~ ~",
                "/\\ dock /\\",
                "|| lamp ||",
            ),
        )

        viewport = render_scene_viewport(frame, width=24, height=5, use_color=False)

        self.assertEqual(
            viewport,
            [
                "SCENE: Harbor".ljust(24),
                "info: low fog".ljust(24),
                "~ ~ ~".ljust(24),
                "/\\ dock /\\".ljust(24),
                "|| lamp ||".ljust(24),
            ],
        )

    def test_render_split_pane_keeps_fixed_left_rail_and_fills_remaining_width(self) -> None:
        status = ShellStatus(
            mode="tool_call",
            turn_number=12,
            scene_id="scene:harbor",
            model_name="llama3.1",
            tool_name="session.read",
            message="checking the harbor state",
        )
        frame = SceneFrame(
            title="Harbor",
            lines=(
                "~~~ fog ~~~",
                "/\\ lighthouse /\\",
                "|| docks ||",
            ),
        )

        layout = render_split_pane(
            status,
            frame,
            width=56,
            height=6,
            rail_width=18,
            use_color=False,
        )

        lines = layout.splitlines()
        self.assertEqual(len(lines), 6)
        self.assertTrue(all(len(line) == 56 for line in lines))
        self.assertTrue(all(line[18] == "|" for line in lines))
        self.assertIn("tool: session.read", layout)
        self.assertIn("SCENE: Harbor", layout)
        self.assertIn("/\\ lighthouse /\\", layout)
        self.assertNotIn("\x1b[", layout)

    def test_render_split_pane_preserves_colored_scene_clipping(self) -> None:
        snapshot = {
            "scene_id": "scene:harbor",
            "summary_text": "A long foggy harbor description that should clip cleanly.",
            "state": {
                "facts": {"weather": "foggy", "time": "midnight"},
                "notes": ["watch the lights", "stay quiet in the fog"],
            },
            "graph_focus": {"entity_ids": [1, 2, 3]},
        }
        status = ShellStatus(
            mode="spinner",
            turn_number=12,
            scene_id="scene:harbor",
            model_name="llama3.1",
            message="loading scene",
            spinner_index=1,
        )
        rendered_frame = render_scene_frame(snapshot, width=32, height=6, use_color=True)
        frame = SceneFrame(title="Harbor", subtitle="fog layer", lines=rendered_frame.lines)

        layout = render_split_pane(
            status,
            frame,
            width=48,
            height=6,
            rail_width=16,
            use_color=True,
        )

        lines = layout.splitlines()
        self.assertEqual(len(lines), 6)
        self.assertIn("\x1b[", layout)
        self._assert_no_partial_ansi(layout)
        self.assertTrue(all(len(self._strip_ansi(line)) == 48 for line in lines))


if __name__ == "__main__":
    unittest.main()
