"""Regression test for the Bridge SessionPool queue (the fix for 500/503 storms).

Verifies the fair waiting-room behavior without a real Chrome extension:
many concurrent requests sharing few sessions must all succeed, serialized
in waves, with no "No available session" errors.

Run:
    cd ~/deepseek-api/backend
    ../venv/bin/python -m pytest tests/test_session_pool.py -q
or:
    ../venv/bin/python tests/test_session_pool.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ws_server


class FakeWS:
    """Minimal stand-in for a Starlette WebSocket the SessionManager talks to."""
    def __init__(self):
        self.sent = []

    async def send_text(self, data):
        self.sent.append(data)


def _make_pool_with_sessions(n):
    pool = ws_server.SessionPool()
    for i in range(n):
        sm = ws_server.SessionManager(FakeWS(), f"s{i}")
        pool.sessions[f"s{i}"] = sm
    return pool


def _patch_session_run(pool, work_time=0.05):
    """Replace SessionManager.send_request with a fake that just sleeps and
    returns success — so we test the POOL's queueing, not the websocket."""
    async def fake_send(self, payload):
        await asyncio.sleep(work_time)
        return {"success": True, "content": f"ok:{payload.get('prompt','')}"}
    # bind per instance
    for sm in pool.sessions.values():
        sm.send_request = fake_send.__get__(sm, ws_server.SessionManager)


async def _run(n_sessions, n_requests):
    pool = _make_pool_with_sessions(n_sessions)
    _patch_session_run(pool, work_time=0.05)

    async def one(i):
        return await pool.send_request({"prompt": str(i)}, queue_timeout=10)

    results = await asyncio.gather(*[one(i) for i in range(n_requests)],
                                   return_exceptions=True)
    return pool, results


def test_all_requests_succeed_under_contention():
    """12 requests, 4 sessions -> all succeed, none raise."""
    pool, results = asyncio.run(_run(4, 12))
    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, f"unexpected errors: {errors}"
    assert all(r.get("success") for r in results)
    # every session must be released back to not-busy at the end
    assert all(not s.is_busy for s in pool.sessions.values())


def test_no_sessions_raises_cleanly():
    """No sessions connected -> Runtime error 'No extension connected', not a hang."""
    async def go():
        pool = ws_server.SessionPool()  # created inside a running loop (py3.9)
        return await pool.send_request({"prompt": "x"}, queue_timeout=1)

    try:
        asyncio.run(go())
        assert False, "should have raised"
    except RuntimeError as e:
        assert "No extension connected" in str(e)


def test_single_session_serializes():
    """6 requests, 1 session -> all succeed sequentially."""
    pool, results = asyncio.run(_run(1, 6))
    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors
    assert len(results) == 6 and all(r.get("success") for r in results)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = []
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            print("FAIL", fn.__name__, e)
            failed.append(fn.__name__)
    print(f"\n{len(fns) - len(failed)}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
