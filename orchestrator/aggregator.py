"""Aggregator — collapse plan step results into one final answer (deep mode).

Uses one DeepSeek call to synthesize the per-step results into a coherent
answer addressed to the user.
"""
import logging
from typing import List, Dict, Any

from bridge_client import bridge

logger = logging.getLogger("orchestrator.aggregator")

AGG_PROMPT = """You are the aggregator. Combine the results of all completed
steps into a single, coherent final answer for the user. Do not mention the
step structure unless it helps. Be complete but concise.

ORIGINAL TASK:
{task}

STEP RESULTS:
{results}

FINAL ANSWER:"""


async def aggregate(task: str, plan: List[Dict[str, Any]],
                    results: Dict[int, str]) -> str:
    lines = []
    for s in plan:
        sid = s["step"]
        lines.append(f"[Step {sid}: {s['description']}]\n{results.get(sid, '(no result)')}")
    results_block = "\n\n".join(lines)
    prompt = AGG_PROMPT.replace("{task}", task).replace("{results}", results_block)
    try:
        return await bridge.complete(prompt)
    except Exception as e:
        logger.warning("aggregator failed: %s", e)
        # fallback: just concatenate
        return "\n\n".join(f"{s['description']}: {results.get(s['step'], '')}" for s in plan)
