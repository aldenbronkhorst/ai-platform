"""Put the harness dir on sys.path so the flat modules import as top-level names.

The harness is a standalone tool (not part of an app package), so tests and the
runner import siblings directly (e.g. `from extract import ...`). pytest loads
this conftest automatically before collecting evals/harness/tests.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
