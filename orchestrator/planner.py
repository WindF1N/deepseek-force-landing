"""Planner — decompose a complex task into a JSON plan (deep mode).

Uses ONE fast DeepSeek call to break the user's task into 3-7 steps with
dependencies. Falls back to a single trivial step if planning fails or the
task is simple.
"""
import json
import logging
from typing import List, Dict, Any

import toolcall as toolcall
from bridge_client import bridge

logger = logging.getLogger("orchestrator.planner")

PLANNER_PROMPT = """You are a task planner. Break the task below into 3-7 logical steps.
For each step provide:
  - step: integer id
  - description: short imperative description
  - depends_on: list of step ids this step needs completed first (empty if none)
  - parallel_ok: true if this step can run at the same time as its siblings

Return ONLY a JSON object, no prose, no markdown:
{"plan": [{"step": 1, "description": "...", "depends_on": [], "parallel_ok": true}]}

TASK:
{task}
"""


async def make_plan(task: str) -> List[Dict[str, Any]]:
    prompt = PLANNER_PROMPT.replace("{task}", task)
    try:
        raw = await bridge.complete(prompt, model="deepseek-expert")
    except Exception as e:
        logger.warning("planner call failed: %s", e)
        return [{"step": 1, "description": task, "depends_on": [], "parallel_ok": False}]

    calls, content, how = toolcall.parse(raw)
    # planner is supposed to return {"plan":[...]}, parse generically
    obj = None
    try:
        obj = json.loads(raw.strip())
    except Exception:
        # reuse toolcall's balanced extractor
        bo = toolcall._balanced_object(raw)
        if bo:
            try:
                obj = json.loads(bo)
            except Exception:
                obj = None

    if isinstance(obj, dict) and isinstance(obj.get("plan"), list) and obj["plan"]:
        plan = []
        for i, step in enumerate(obj["plan"], 1):
            plan.append({
                "step": step.get("step", i),
                "description": step.get("description", ""),
                "depends_on": step.get("depends_on", []) or [],
                "parallel_ok": bool(step.get("parallel_ok", False)),
            })
        return plan

    logger.info("planner returned no usable plan, single-step fallback")
    return [{"step": 1, "description": task, "depends_on": [], "parallel_ok": False}]
