"""Every source file must compile on the oldest Python the package claims to support.

`dg status` shipped with a nested f-string reusing the same quote character inside its
expression — legal only under PEP 701, so Python 3.12 and later. `pyproject.toml` declares
`requires-python = ">=3.11"`, but the Dockerfile pins `python:3.13-slim`, so every
containerised command parsed it happily. The declared floor and the exercised floor were
different numbers, and the only way to find out was to run the package outside the
container on 3.11.

**`ast.parse(..., feature_version=(3, 11))` does not catch this.** That was the first
attempt at this guard, and it passed on the broken file: `feature_version` gates a short
list of grammar features and does not restore the pre-3.12 f-string tokenizer. A check that
cannot fail is worse than no check, because it reports success — so this runs a genuine
interpreter at the declared floor instead, and `test_the_check_is_not_vacuous` proves the
mechanism still rejects the exact syntax that shipped.

Requires Docker; skipped without it, since the containerised `dg` already needs Docker and
the host is not assumed to have a 3.11 interpreter lying around.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The shape that shipped: an f-string nested in another f-string's expression, reusing the
# same quote character.
PEP701_ONLY = """value = f"{bold(r['name'])}  {dim(f'{r['nodes']:,} artifacts')}"\n"""


def declared_floor() -> tuple[int, int]:
    """The minimum Python version from pyproject.toml's requires-python.

    Read rather than hardcoded: raising `requires-python` later would otherwise leave this
    guard quietly checking a version nobody supports any more.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'requires-python\s*=\s*"[^"]*?(\d+)\.(\d+)', text)
    if not match:
        raise AssertionError("could not read requires-python from pyproject.toml")
    return int(match.group(1)), int(match.group(2))


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(
        ["docker", "info"], capture_output=True, timeout=60
    ).returncode == 0


def compile_under_floor(directory: Path, targets: list[str]) -> subprocess.CompletedProcess:
    major, minor = declared_floor()
    return subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{directory}:/w",
            "-w", "/w",
            f"python:{major}.{minor}-slim",
            "python", "-m", "compileall", "-q", *targets,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )


@unittest.skipUnless(_docker_available(), "Docker not available")
class PythonFloorTest(unittest.TestCase):
    def test_pyproject_declares_a_floor(self) -> None:
        major, minor = declared_floor()
        self.assertEqual(major, 3)
        self.assertGreaterEqual(minor, 8)

    def test_package_compiles_at_the_declared_floor(self) -> None:
        result = compile_under_floor(ROOT, ["src", "tests", "eval"])
        floor = declared_floor()
        self.assertEqual(
            result.returncode,
            0,
            f"source does not compile on Python {floor[0]}.{floor[1]}, which "
            f"pyproject.toml claims to support:\n{result.stdout}{result.stderr}",
        )

    def test_the_check_is_not_vacuous(self) -> None:
        # If this ever passes, the guard above has stopped guarding — which is exactly how
        # the first version of this test failed silently.
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "pep701_sample.py"
            bad.write_text(PEP701_ONLY, encoding="utf-8")
            result = compile_under_floor(Path(tmp), ["pep701_sample.py"])
        self.assertNotEqual(
            result.returncode,
            0,
            "the floor check accepted syntax that is invalid at the declared floor",
        )
        self.assertIn("SyntaxError", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
