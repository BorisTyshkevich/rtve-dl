import argparse
import unittest
from pathlib import Path
from unittest.mock import patch

from rtve_dl.cli import _resolve_model_flag
from rtve_dl.codex_batch import _build_backend_command


class GeminiCliTests(unittest.TestCase):
    def test_resolve_model_flag_routes_generic_model_to_gemini(self) -> None:
        args = argparse.Namespace(
            model="gemini-2.0-flash",
            translation_backend="gemini",
            claude_model="sonnet",
            codex_model="gpt-5.1-codex-mini",
            gemini_model=None,
        )

        _resolve_model_flag(args)

        self.assertEqual(args.gemini_model, "gemini-2.0-flash")
        self.assertEqual(args.claude_model, "sonnet")
        self.assertEqual(args.codex_model, "gpt-5.1-codex-mini")


class GeminiCommandTests(unittest.TestCase):
    @patch("rtve_dl.codex_batch._ensure_gemini_on_path")
    def test_build_backend_command_matches_rust_shape_with_model(self, _ensure: object) -> None:
        cmd, writes_stdout_to_tsv = _build_backend_command(
            backend="gemini",
            model="gemini-2.5-pro",
            out_tsv=Path("tmp/out.tsv"),
        )

        self.assertEqual(
            cmd,
            [
                "gemini",
                "-p",
                "",
                "--output-format",
                "text",
                "--allowed-mcp-server-names",
                "",
                "--model",
                "gemini-2.5-pro",
            ],
        )
        self.assertTrue(writes_stdout_to_tsv)

    @patch("rtve_dl.codex_batch._ensure_gemini_on_path")
    def test_build_backend_command_allows_gemini_default_model(self, _ensure: object) -> None:
        cmd, writes_stdout_to_tsv = _build_backend_command(
            backend="gemini",
            model=None,
            out_tsv=Path("tmp/out.tsv"),
        )

        self.assertNotIn("--model", cmd)
        self.assertTrue(writes_stdout_to_tsv)


if __name__ == "__main__":
    unittest.main()
