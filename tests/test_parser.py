import sys
import unittest

import mdrun.parser as p
import mdrun.render as r
import mdrun.runner as ru
from mdrun.parser import CodeBlock, Heading, Paragraph, parse


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.text = (
            "# Title\n"
            "Some *intro* text.\n"
            "\n"
            "## Step one\n"
            "```bash\necho hi\n```\n"
            "\n"
            "- item a\n"
            "- item b\n"
            "\n"
            "```python\nprint(1)\n```\n"
        )
        self.elements, self.blocks = parse(self.text)

    def test_block_count(self):
        self.assertEqual(len(self.blocks), 2)

    def test_block_content(self):
        self.assertEqual(self.blocks[0].lang, "bash")
        self.assertEqual(self.blocks[0].lines, ["echo hi"])
        self.assertEqual(self.blocks[1].lang, "python")
        self.assertEqual(self.blocks[1].lines, ["print(1)"])

    def test_labels(self):
        self.assertEqual(self.blocks[0].label, "Step one")

    def test_heading_and_paragraph(self):
        self.assertIsInstance(self.elements[0], Heading)
        self.assertEqual(self.elements[0].level, 1)
        self.assertIsInstance(self.elements[1], Paragraph)

    def test_unterminated_fence_consumes_rest(self):
        el, blocks = parse("```bash\necho a\n```\n\n```python\nprint(2)\nnot closed")
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[1].lines, ["print(2)", "not closed"])

    def test_indented_code(self):
        el, blocks = parse("    code line 1\n    code line 2\n")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].lines, ["code line 1", "code line 2"])
        self.assertEqual(blocks[0].lang, "")


class RenderTests(unittest.TestCase):
    def test_inline_bold_and_code(self):
        spans = r.inline("see **bold** and `code` here")
        joined = "".join(s.text for s in spans)
        self.assertEqual(joined, "see bold and code here")
        bold = [s for s in spans if s.style.bold]
        self.assertTrue(bold and bold[0].text == "bold")

    def test_wrap_respects_width(self):
        spans = r.inline("aaaaaaaa bb cccccc")
        lines = r.wrap(spans, 10)
        joined = [r.span_width(ln) for ln in lines]
        self.assertTrue(all(w <= 10 for w in joined))

    def test_code_lines_mark_selection(self):
        block = CodeBlock(index=0, lang="sh", lines=["echo a", "echo b"], start=1, end=3)
        rows = r.code_lines(block, 40, True)
        first = "".join(s.text for s in rows[0])
        self.assertTrue(first.startswith("  \u276f"))


class RunnerTests(unittest.TestCase):
    def test_shell_block_is_passed_verbatim(self):
        block = CodeBlock(index=0, lang="bash", lines=["export FOO=1", "echo ok"], start=1, end=2)
        cmd, cleanup = ru.block_command(block)
        self.assertIsNone(cleanup)
        self.assertIn("export FOO=1", cmd)

    def test_python_block_goes_through_interpreter(self):
        block = CodeBlock(index=0, lang="python", lines=["print('hi')"], start=1, end=1)
        cmd, cleanup = ru.block_command(block)
        self.assertIsNotNone(cleanup)
        self.assertTrue(cmd.startswith("python3 "))

    def test_unknown_lang_uses_name_as_command(self):
        block = CodeBlock(index=0, lang="cowsay", lines=["mo"], start=1, end=1)
        cmd, cleanup = ru.block_command(block)
        self.assertTrue(cmd.startswith("cowsay "))


if __name__ == "__main__":
    unittest.main(verbosity=2)