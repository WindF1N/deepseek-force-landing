"""Big-file regeneration — defeat DeepSeek's truncated-tool_call ceiling.

DeepSeek-web cannot reliably stream a huge tool_call: a write_file whose
`content` is thousands of lines makes the model break mid-JSON (raw HTML, bash
heredoc, or a hard stop). We can't fix the model, so we change the shape of the
work: instead of asking for one giant write_file, we ask DeepSeek for the file
body in SECTIONS, each small enough to never truncate, then concatenate and hand
Hermes a SINGLE complete write_file. Hermes' tools stay untouched (write_file is
overwrite-only; we just feed it whole content).

Trigger: a parsed write_file whose content arrived truncated (how="salvaged"/
"extracted") OR exceeds CHUNK_THRESHOLD. We re-ask the model for the full file
in N parts using continuation prompts and stitch them. Best-effort: if a part
fails we keep what we have.
"""
import json
import logging
import re
from typing import Optional, Tuple

from bridge_client import bridge

logger = logging.getLogger("orchestrator.bigfile")

CHUNK_THRESHOLD = 1500          # chars; below this a single write_file is fine
MAX_PARTS = 12                  # safety cap on continuation rounds
END_MARKER = "<<<EOF_BIGFILE>>>"

_WRITE_TOOLS = {"write_file", "create_file"}

GEN_HEAD = (
    "Produce ONLY the raw file content for `{path}`. No prose, no markdown "
    "fences, no JSON. Begin at the very first byte. When the file is fully "
    "done, output {marker} on its own line. If it does not all fit, stop at a "
    "natural line boundary and do NOT output the marker.\n\nFILE TASK:\n{task}"
)

GEN_CONT = (
    "Continue the file `{path}` EXACTLY where it left off. Output only the next "
    "part, raw, no prose. Do not repeat earlier lines. Output {marker} on its "
    "own line when complete.\n\nSO FAR (tail):\n{tail}"
)


def is_big_write(name: str, args: dict, how: str) -> bool:
    if name not in _WRITE_TOOLS:
        return False
    content = args.get("content", "")
    if how in ("salvaged",) :
        return True            # truncated -> always regenerate
    return isinstance(content, str) and len(content) >= CHUNK_THRESHOLD


def _strip_marker(s: str) -> Tuple[str, bool]:
    if END_MARKER in s:
        return s.split(END_MARKER)[0], True
    return s, False


async def regenerate(path: str, task: str, *, model: str = "deepseek-expert") -> str:
    """Rebuild the full file body section by section. Returns concatenated text."""
    full = ""
    prompt = GEN_HEAD.replace("{path}", path).replace("{task}", task).replace("{marker}", END_MARKER)
    for part in range(MAX_PARTS):
        try:
            chunk = await bridge.complete(prompt, model=model)
        except Exception as e:
            logger.warning("bigfile part %d failed: %s", part, e)
            break
        chunk = re.sub(r"^```[a-zA-Z]*\n?|```$", "", chunk.strip())
        body, done = _strip_marker(chunk)
        full += body if full.endswith("\n") or not full else "\n" + body
        if done or len(body) < 200:
            break
        tail = full[-800:]
        prompt = GEN_CONT.replace("{path}", path).replace("{tail}", tail).replace("{marker}", END_MARKER)
    logger.info("bigfile regenerated %s: %d chars in <=%d parts", path, len(full), part + 1)
    return full.strip() + "\n"
