"""Wraps the Node-based SSE parser test so it runs under the canonical pytest
command. Verifies the rewritten parser against REAL captured DeepSeek streams
(backend/tests/fixtures/sse_*.txt) for normal / thinking / search modes.

Skips cleanly if node is unavailable.
"""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NODE_TEST = os.path.join(ROOT, "chrome-extension", "test_sse_parser.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_sse_parser_against_real_streams():
    assert os.path.exists(NODE_TEST), "node SSE test missing"
    r = subprocess.run(["node", NODE_TEST], capture_output=True, text=True)
    # surface node output on failure for debugging
    assert r.returncode == 0, f"node SSE parser test failed:\n{r.stdout}\n{r.stderr}"
    assert "ALL PASSED" in r.stdout


if __name__ == "__main__":
    r = subprocess.run(["node", NODE_TEST], capture_output=True, text=True)
    print(r.stdout)
    if r.stderr:
        print("STDERR:", r.stderr)
    raise SystemExit(r.returncode)
