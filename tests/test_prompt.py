import unittest

from mind_game.prompt import build_system_prompt, is_exit_command, normalize_user_input


class PromptTests(unittest.TestCase):
    def test_build_system_prompt_guides_game_design_conversation(self) -> None:
        prompt = build_system_prompt()

        self.assertIn("AI-evolving game prototype", prompt)
        self.assertIn("one concise question at a time", prompt)
        self.assertIn("tone, setting, challenge level", prompt)

    def test_normalize_user_input_trims_whitespace(self) -> None:
        self.assertEqual(normalize_user_input("  hello  "), "hello")

    def test_is_exit_command_recognizes_quit_words(self) -> None:
        self.assertTrue(is_exit_command("exit"))
        self.assertTrue(is_exit_command(" quit "))
        self.assertFalse(is_exit_command("hello"))


if __name__ == "__main__":
    unittest.main()
