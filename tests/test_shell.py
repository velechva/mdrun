import os
import shutil
import tempfile
import unittest
from pathlib import Path

from mdrun.runner import run_block
from mdrun.shell import Shell

MD = """
# T

```bash
export DEMO_VAR="persisted-123"
echo "first output"
```

```bash
echo "var value: $DEMO_VAR"
```

```bash
cd /tmp
mkdir -p mdrun-shell-test
pwd
```

```python
print("python ok " + "!")
```
"""


class ShellIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["SHELL"] = "/bin/bash"
        cls.shell = Shell(shell="/bin/bash", no_rcs=True)
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(MD)
            cls.md_path = fh.name

    @classmethod
    def tearDownClass(cls):
        cls.shell.close()
        os.unlink(cls.md_path)
        shutil.rmtree("/tmp/mdrun-shell-test", ignore_errors=True)

    def _block(self, index):
        from mdrun.parser import parse

        _els, blocks = parse(Path(self.md_path).read_text())
        return blocks[index]

    def _run(self, index):
        return run_block(self.shell, self._block(index), timeout=30)

    def test_output_and_exit_codes(self):
        code, out = self._run(0)
        self.assertEqual(code, 0)
        self.assertIn("first output", out)

    def test_env_persists_across_blocks(self):
        self._run(0)
        code, out = self._run(1)
        self.assertEqual(code, 0)
        self.assertIn("persisted-123", out)

    def test_cwd_persists_across_blocks(self):
        from mdrun.parser import parse

        doc = (
            "```bash\n"
            "mkdir -p /tmp/mdrun-shell-test && cd /tmp/mdrun-shell-test\n"
            "echo placed\n"
            "```\n\n"
            "```bash\n"
            "pwd\n"
            "```\n"
        )
        blocks = parse(doc)[1]
        code, out = run_block(self.shell, blocks[0], timeout=30)
        self.assertEqual(code, 0)
        self.assertIn("placed", out)
        code, out = run_block(self.shell, blocks[1], timeout=30)
        self.assertEqual(code, 0)
        self.assertIn("/tmp/mdrun-shell-test", out)

    def test_python_block_runs(self):
        code, out = self._run(3)
        self.assertEqual(code, 0)
        self.assertIn("python ok !", out)

    def test_failing_command_returns_nonzero(self):
        from mdrun.parser import parse

        _els, blocks = parse("```bash\nfalse\n```")
        code, out = run_block(self.shell, blocks[0], timeout=30)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)