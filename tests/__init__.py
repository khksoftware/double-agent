# SPDX-License-Identifier: Apache-2.0
"""Present so the suite is a package and its modules can share fixtures by relative import.

Without it, pytest imports each test module as a top-level module and every
``from .conftest import ...`` fails with "attempted relative import with no known parent
package" -- an error about packaging that reads like an error about the code under test.
"""
