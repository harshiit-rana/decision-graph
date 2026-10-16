"""Make `src/` importable when the package isn't installed (#123).

    python -m unittest discover -s tests -t .

`-t .` makes `tests` a package, so this file runs before any test module imports
`decision_graph`. Without it, a contributor who hadn't run `pip install -e .` got 16 import
errors, which looked like broken code. `src` goes first on `sys.path`, so the checkout under
test is always the one imported, even if an older copy is installed. CI runs discovery a
second time in a venv without the package.
"""

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
