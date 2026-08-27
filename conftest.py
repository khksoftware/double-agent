# SPDX-License-Identifier: Apache-2.0
"""Makes the package importable for its own test suite without an install step.

pytest loads every ``conftest.py`` between its rootdir and the collected test files, so
running ``python -m pytest tests`` from this directory -- or naming this directory from one
level up -- picks this up automatically.

**Why the path is inserted rather than assumed.** This is a ``src`` layout, so the import
package is not a subdirectory of the repository root. A bare ``import double_agent`` from
the root with no path wiring can silently resolve to an EMPTY NAMESPACE PACKAGE rather than
failing: the import succeeds, every attribute lookup fails later, and a test suite that only
imports the module and checks it exists passes against nothing at all. Asserting on
``double_agent.__file__`` is the check that distinguishes the two, and this package's own
suite performs it.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
for entry in (SRC, ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
