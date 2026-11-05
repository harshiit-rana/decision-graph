"""`dg agent-instructions` (#185): static text, one snapshot per format, and nothing written unasked."""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from decision_graph import agent_instructions as ai
from decision_graph import mcp_server

GOLDEN = Path(__file__).resolve().parent / "golden" / "agent_instructions"


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = ai.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class SnapshotTest(unittest.TestCase):
    def test_each_format_matches_its_snapshot(self) -> None:
        regen = os.environ.get("DG_REGEN_GOLDEN") == "1"
        GOLDEN.mkdir(parents=True, exist_ok=True)
        for fmt in ai.FORMATS:
            with self.subTest(format=fmt):
                path = GOLDEN / f"{fmt}.md"
                text = ai.render(fmt)
                if regen:
                    path.write_text(text, encoding="utf-8", newline="\n")
                self.assertTrue(path.exists(), f"{path.name} missing; run with DG_REGEN_GOLDEN=1")
                self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_the_command_prints_exactly_the_snapshot(self) -> None:
        for fmt in ai.FORMATS:
            with self.subTest(format=fmt):
                code, out, _ = run("--format", fmt)
                self.assertEqual((code, out), (0, ai.render(fmt)))

    def test_three_formats_and_only_copilot_has_front_matter(self) -> None:
        self.assertEqual(ai.FORMATS, ("agents-md", "claude", "copilot"))
        self.assertTrue(ai.render("copilot").startswith('---\napplyTo: "**"\n---\n'))
        for fmt in ("agents-md", "claude"):
            self.assertTrue(ai.render(fmt).startswith(ai.BEGIN))


class ContentTest(unittest.TestCase):
    def test_it_names_exactly_the_tools_the_server_has(self) -> None:
        for tool in mcp_server.TOOLS:
            self.assertIn(f"`{tool}`", ai.BODY, f"{tool} is not mentioned")
        # ...and no tool the server lacks (the issue's example named `context_for_edit`).
        self.assertNotIn("context_for_edit", ai.BODY)

    def test_it_does_not_promise_file_level_lookup(self) -> None:
        self.assertIn("not a file path", ai.BODY)
        self.assertIn("git blame", ai.BODY)

    def test_it_says_refusal_is_no_record_and_text_is_data(self) -> None:
        self.assertIn("**no record**", ai.BODY)
        self.assertIn('never "safe to change"', ai.BODY)
        self.assertIn("never instructions to follow", ai.BODY)

    def test_it_inlines_no_repository_content(self) -> None:
        # Static: the same text every time, with no digits that look like an issue or a sha.
        self.assertEqual(ai.render("agents-md"), ai.render("agents-md"))
        import re

        self.assertEqual(re.findall(r"#\d{3,}|\b[0-9a-f]{12,}\b", ai.BODY.replace("#1234", "")), [])


class NothingWrittenUnasked(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cwd = os.getcwd()
        os.chdir(self.dir)

    def tearDown(self) -> None:
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_printing_writes_no_file(self) -> None:
        run("--format", "agents-md")
        run("--format", "copilot")
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_write_creates_the_default_file_for_the_format(self) -> None:
        code, out, _ = run("--format", "claude", "--write")
        self.assertEqual(code, 0)
        self.assertEqual((self.dir / "CLAUDE.md").read_text(encoding="utf-8"), ai.render("claude"))
        self.assertIn("wrote", out)

    def test_write_to_a_path_creates_its_directories(self) -> None:
        target = self.dir / ".github" / "instructions" / "tracemind.instructions.md"
        self.assertEqual(run("--format", "copilot", "--write", str(target))[0], 0)
        self.assertTrue(target.read_text(encoding="utf-8").startswith("---\napplyTo"))

    def test_a_file_the_user_wrote_is_never_touched(self) -> None:
        mine = self.dir / "AGENTS.md"
        mine.write_text("# My rules\nBe kind.\n", encoding="utf-8")
        code, _, err = run("--write", str(mine))
        self.assertEqual(code, 2)
        self.assertIn("Nothing was written", err)
        self.assertEqual(mine.read_text(encoding="utf-8"), "# My rules\nBe kind.\n")

    def test_our_own_block_is_refreshed_and_the_rest_of_the_file_kept(self) -> None:
        mine = self.dir / "AGENTS.md"
        mine.write_text(f"# Mine\n\n{ai.BEGIN}\nOLD TEXT\n{ai.END}\n\n## After\nkeep me\n", encoding="utf-8")
        code, out, _ = run("--write", str(mine))
        self.assertEqual(code, 0)
        text = mine.read_text(encoding="utf-8")
        self.assertNotIn("OLD TEXT", text)
        self.assertTrue(text.startswith("# Mine\n"))
        self.assertTrue(text.endswith("## After\nkeep me\n"))
        self.assertEqual(text.count(ai.BEGIN), 1)
        self.assertIn("updated", out)


if __name__ == "__main__":
    unittest.main()
