"""Executor — run plan steps with dependency-aware scheduling (deep mode).

Steps with satisfied dependencies and parallel_ok run concurrently via
asyncio.gather (bounded by the Bridge's session semaphore). Sequential steps
run one at a time, each seeing prior results as context.
"""
import asyncio
import logging
from typing import List, Dict, Any

from bridge_client import bridge

logger = logging.getLogger("orchestrator.executor")

STEP_PROMPT = """You are executing one step of a larger plan.

ORIGINAL TASK:
{task}

COMPLETED STEPS AND THEIR RESULTS:
{context}

YOUR CURRENT STEP:
{description}

Do this step now. Respond with the concrete result/answer for this step only,
in plain text. Be precise and self-contained.
"""


async def _run_step(task: str, step: Dict[str, Any],
                    results: Dict[int, str]) -> str:
    ctx_lines = []
    for dep in step.get("depends_on", []):
        if dep in results:
            ctx_lines.append(f"[Step {dep}] {results[dep]}")
    context = "\n".join(ctx_lines) if ctx_lines else "(none)"
    prompt = (STEP_PROMPT
              .replace("{task}", task)
              .replace("{context}", context)
              .replace("{description}", step["description"]))
    try:
        return await bridge.complete(prompt)
    except Exception as e:
        logger.warning("step %s failed: %s", step.get("step"), e)
        return f"[ERROR executing step {step.get('step')}: {e}]"


async def execute(task: str, plan: List[Dict[str, Any]]) -> Dict[int, str]:
    """Execute the plan respecting dependencies. Returns {step_id: result}."""
    results: Dict[int, str] = {}
    remaining = {s["step"]: s for s in plan}

    while remaining:
        # find steps whose deps are all satisfied
        ready = [s for s in remaining.values()
                 if all(d in results for d in s.get("depends_on", []))]
        if not ready:
            # dependency cycle / unsatisfiable — run the rest sequentially
            ready = list(remaining.values())

        # split into a parallel batch and sequential ones
        parallel = [s for s in ready if s.get("parallel_ok")]
        sequential = [s for s in ready if not s.get("parallel_ok")]

        if len(parallel) > 1:
            outs = await asyncio.gather(*[_run_step(task, s, results) for s in parallel])
            for s, out in zip(parallel, outs):
                results[s["step"]] = out
                remaining.pop(s["step"], None)
        else:
            sequential = parallel + sequential

        for s in sequential:
            results[s["step"]] = await _run_step(task, s, results)
            remaining.pop(s["step"], None)

    return results
