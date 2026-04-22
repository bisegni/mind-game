import unittest
from unittest.mock import patch

import main


class MainTests(unittest.TestCase):
    def test_parse_args_reads_model_argument(self) -> None:
        with patch("sys.argv", ["main.py", "--model", "gpt-oss:120b"]):
            args = main.parse_args()

        self.assertEqual(args.model, "gpt-oss:120b")

    def test_parse_args_reads_base_url_argument(self) -> None:
        with patch("sys.argv", ["main.py", "--base-url", "http://localhost:11434"]):
            args = main.parse_args()

        self.assertEqual(args.base_url, "http://localhost:11434")

    def test_resolve_base_url_prefers_ollama_host(self) -> None:
        with patch.dict("os.environ", {"OLLAMA_HOST": "remote.example.com:11434"}, clear=True):
            self.assertEqual(main.resolve_base_url(), "http://remote.example.com:11434")

    def test_resolve_base_url_uses_ollama_host_url_as_is(self) -> None:
        with patch.dict("os.environ", {"OLLAMA_HOST": "https://remote.example.com:11434"}, clear=True):
            self.assertEqual(main.resolve_base_url(), "https://remote.example.com:11434")

    def test_resolve_base_url_adds_default_port_when_missing(self) -> None:
        with patch.dict("os.environ", {"OLLAMA_HOST": "slacstudio.local"}, clear=True):
            self.assertEqual(main.resolve_base_url(), "http://slacstudio.local:11434")

    def test_resolve_base_url_adds_default_port_to_schemed_host(self) -> None:
        with patch.dict("os.environ", {"OLLAMA_HOST": "http://slacstudio.local"}, clear=True):
            self.assertEqual(main.resolve_base_url(), "http://slacstudio.local:11434")

    def test_chunk_text_reads_content(self) -> None:
        class Chunk:
            content = "hello"

        self.assertEqual(main.chunk_text(Chunk()), "hello")


if __name__ == "__main__":
    unittest.main()
