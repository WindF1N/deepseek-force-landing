"""Orchestrator API (port 8002) — OpenAI-compatible front for Hermes.

Two modes:
  - tool_loop (DEFAULT): transparent per-turn proxy. Hermes runs its own agent
    loop and calls us once per turn. We render the request to a flat prompt,
    fan out N parallel DeepSeek calls (self-consistency), vote, validate,
    recover, and return a NATIVE OpenAI tool_calls response so Hermes can
    dispatch the tool itself. This is what makes a swap-in provider work.

  - deep: full Planner -> Executor -> Validator -> Aggregator pipeline for a
    single complex prompt (no Hermes tool loop). Exposed as model name
    containing "deep", or via header X-Orch-Mode: deep.
"""
import logging
import re
import time
import uuid
from typing import List, Optional, Union

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

import json
from config import settings
from bridge_client import bridge
import toolcall
import prompt as prompt_builder
import validator
import planner
import executor
import aggregator
import bigfile

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("orchestrator.api")

app = FastAPI(title="DeepSeek Orchestrator")


# ---------------- OpenAI schema ----------------
class ChatMessage(BaseModel):
    role: str
    content: Union[str, list, None] = None
    tool_calls: Optional[List[dict]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class ChatRequest(BaseModel):
    model: str = "deepseek-chat"
    messages: List[ChatMessage]
    tools: Optional[List[dict]] = None
    tool_choice: Optional[Union[str, dict]] = None
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    user: Optional[str] = None  # OpenAI-standard field for session tracking


# ---------------- helpers ----------------
_DONE_CLAIM_RE = re.compile(
    r"(создан|создал|готово|сделал|сделано|записал|перезаписал|обновил|"
    r"по\s+(пути|адресу)|по\s+пути|index\.html|"
    r"created|written|saved|done|updated|overwritten|file is at)",
    re.IGNORECASE)


def _claims_done(text: str) -> bool:
    """Heuristic: prose that claims work was performed (file made, task done)."""
    if not text:
        return False
    return bool(_DONE_CLAIM_RE.search(text))


_WRITE_TOOLS = {"write_file", "create_file"}


def _write_ever_called(messages: list) -> bool:
    """Did any assistant turn actually call a write/create tool this session?"""
    for m in messages:
        for tc in (m.get("tool_calls") or []):
            if tc.get("function", {}).get("name") in _WRITE_TOOLS:
                return True
    return False


def _model_for_mode(model: str) -> str:
    # Always expert. No chat, no thinking, no search. Vision only if files.
    if "vision" in model:
        return "deepseek-vision"
    return "deepseek-expert"


def _resp_envelope(model: str, *, content: Optional[str] = None,
                   tool_calls: Optional[list] = None) -> dict:
    msg = {"role": "assistant", "content": content}
    finish = "stop"
    if tool_calls:
        msg["tool_calls"] = tool_calls
        msg["content"] = content  # may be None
        finish = "tool_calls"
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ---------------- routes ----------------
@app.get("/health")
async def health():
    bh = await bridge.health()
    return {"status": "ok", "bridge": bh, "vote_n": settings.VOTE_N,
            "default_mode": settings.DEFAULT_MODE}


@app.get("/v1/models")
async def models():
    data = [{"id": m, "object": "model", "owned_by": "deepseek-orchestrator",
             "created": 1700000000}
            for m in ("deepseek-chat", "deepseek-expert", "deepseek-vision",
                      "deepseek-deep")]
    return {"object": "list", "data": data}


@app.get("/models")
async def models2():
    return await models()


async def _heal_big_writes(tool_calls: list, messages: list, ds_model: str) -> list:
    """Defeat truncated huge write_file: regenerate the body section-by-section.

    DeepSeek can't stream a giant content arg in one shot. When we see a
    write_file whose content looks truncated/oversized, we re-ask the Bridge for
    the full file in parts and stitch it, so Hermes gets one complete file.
    """
    task = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            task = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
            break
    healed = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except Exception:
            healed.append(tc); continue
        how = "salvaged" if tc.get("_salvaged") else "clean"
        if bigfile.is_big_write(name, args, how):
            path = args.get("path", "file.txt")
            try:
                full = await bigfile.regenerate(path, task or f"write {path}", model=ds_model)
                if len(full) > len(args.get("content", "")):
                    args["content"] = full
                    fn["arguments"] = json.dumps(args, ensure_ascii=False)
                    logger.info("healed big write_file %s -> %d chars", path, len(full))
            except Exception as e:
                logger.warning("bigfile heal failed for %s: %s", path, e)
        healed.append(tc)
    return healed


async def _run_persistent_chat(req: ChatRequest) -> dict:
    """PERSISTENT CHAT: One Hermes turn using persistent DeepSeek chat context.
    
    Flow:
    1. Generate user_id from conversation hash
    2. Build minimal prompt for current turn only
    3. Send to backend persistent chat via HTTP API
    4. Parse response and return
    
    Backend manages the persistent chat session and context.
    """
    import hashlib
    import httpx
    
    messages = [m.model_dump() for m in req.messages]
    
    # Generate user_id from conversation hash
    # Hash the FIRST user message in the conversation - это стабильный идентификатор сессии!
    # Одна Hermes сессия всегда начинается с одного и того же первого сообщения
    import hashlib
    
    # Найти первое user сообщение
    first_user_msg = None
    for msg in messages:
        if msg.get("role") == "user":
            first_user_msg = msg
            break
    
    if first_user_msg:
        # Хешируем контент первого сообщения
        content = first_user_msg.get("content", "")
        if isinstance(content, list):
            # Image + text content
            content_str = json.dumps(content, sort_keys=True)
        else:
            content_str = str(content)
        
        user_id = hashlib.md5(content_str.encode()).hexdigest()[:16]
        logger.info(f"[SESSION] user_id={user_id} from first message hash")
    else:
        # FALLBACK: используем стабильный ID
        user_id = hashlib.md5(b"hermes_persistent_chat_default").hexdigest()[:16]
        logger.info(f"[SESSION] user_id={user_id} FALLBACK (no user messages)")
    
    ds_model = _model_for_mode(req.model)
    images = prompt_builder.extract_images(messages)
    if images:
        ds_model = "deepseek-vision"
    
    # Build minimal prompt: only current user message + tool results
    # Extract last user message and tool results from history
    last_user_msg = None
    tool_results = []
    
    # CRITICAL FIX: In persistent chat mode (when parent_message_id exists),
    # DeepSeek ALREADY has the previous tool results in its context.
    # We should ONLY send NEW tool results (from current turn).
    # 
    # Strategy: collect tool results that appear AFTER the last assistant message
    # (those are NEW results from current turn that DeepSeek hasn't seen yet)
    last_assistant_idx = None
    for i, m in enumerate(reversed(messages)):
        if m.get("role") == "assistant":
            last_assistant_idx = len(messages) - 1 - i
            break
    
    for i, m in enumerate(messages):
        if m.get("role") == "user" and not last_user_msg:
            # Take LAST user message
            if i > (last_assistant_idx or -1):
                last_user_msg = m
        elif m.get("role") == "tool":
            # Only collect tool results AFTER last assistant response
            # (these are NEW, DeepSeek hasn't seen them yet)
            if last_assistant_idx is None or i > last_assistant_idx:
                tool_results.append(m)
    
    # Build current turn prompt
    if tool_results:
        # After tool execution: send tool results
        prompt_parts = []
        for tr in tool_results:
            tool_name = tr.get("name", "unknown")
            tool_output = tr.get("content", "")
            prompt_parts.append(f"[TOOL {tool_name} RESULT]\n{tool_output}\n")
        current_prompt = "\n".join(prompt_parts)
        
        # CRITICAL: Add format reminder after tool results too
        # (DeepSeek especially forgets after seeing tool output)
        if req.tools:
            current_prompt = prompt_builder.TOOL_CALL_REMINDER + "\n\n" + current_prompt
    elif last_user_msg:
        # Fresh user request
        content = last_user_msg.get("content", "")
        if isinstance(content, list):
            # Extract text from content array
            text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
            current_prompt = "\n".join(text_parts)
        else:
            current_prompt = content
        
        # CRITICAL: Add format reminder to EVERY user message
        # (DeepSeek sometimes forgets and outputs XML instead of JSON)
        if req.tools:
            current_prompt = prompt_builder.TOOL_CALL_REMINDER + "\n\n" + current_prompt
        
        # On first turn: include tools definition
        ctx_info = await _get_context_info(user_id)
        # Check if chat exists (chat_id not None) - if new chat, include system init
        logger.info(f"Context check: chat_id={ctx_info.get('chat_id')}, has_tools={req.tools is not None}")
        if ctx_info.get("chat_id") is None and req.tools:
            tools_text = prompt_builder.build_system_init(messages, req.tools)
            current_prompt = tools_text + "\n\n" + current_prompt
            logger.info(f"Added tools definition (first turn), prompt now {len(current_prompt)} chars")
    else:
        current_prompt = "continue"

    logger.info(current_prompt)
    
    # Send to backend persistent chat
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{settings.BRIDGE_URL}/api/contexts/{user_id}/send",
            json={
                "prompt": current_prompt,
                "model": ds_model,
                "images": images
            }
        )
        
        if response.status_code != 200:
            raise RuntimeError(f"Backend persistent chat failed: {response.status_code} {response.text}")
        
        result = response.json()
    
    if not result.get("success"):
        raise RuntimeError(f"Chat send failed: {result}")
    
    content = result.get("content", "")
    thinking = result.get("thinking")
    token_count = result.get("token_count", 0)
    
    logger.info(f"Persistent chat {user_id}: tokens={token_count}, prompt_len={len(current_prompt)}")
    
    # Parse response for tool calls
    tool_calls, plain_content, parse_method = toolcall.parse(content)
    
    # DEBUG: Log parse result
    logger.info(f"[PARSE DEBUG] content length={len(content)}, parse_method={parse_method}")
    logger.info(f"[PARSE DEBUG] tool_calls={tool_calls is not None}, plain_content={plain_content[:100] if plain_content else None}")
    if content and len(content) < 1000:
        logger.info(f"[PARSE DEBUG] raw content: {content}")
    
    if tool_calls:
        # Validate for debugging, but return tool_calls anyway - Hermes can fix minor issues
        ok, err = validator.validate_tool_calls(tool_calls, req.tools)
        if not ok:
            logger.warning(f"Tool validation failed: {err}, but returning tool_calls anyway (Hermes will fix)")
        
        # Check for big file healing
        healed = await _heal_big_writes(tool_calls, messages, ds_model)
        # Return both reasoning (content) AND tool_calls - OpenAI API allows this
        # Hermes will show content as text, then execute tools
        result = _resp_envelope(req.model, content=plain_content, tool_calls=healed)
        logger.info(f"[RESPONSE DEBUG] Returning tool_calls response: {json.dumps(result, ensure_ascii=False)[:500]}")
        return result
    else:
        # Plain text response
        return _resp_envelope(req.model, content=content)


async def _get_context_info(user_id: str) -> dict:
    """Get persistent chat context info from backend."""
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.get(f"{settings.BRIDGE_URL}/api/contexts/{user_id}")
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.warning(f"Failed to get context info: {e}")
    return {"message_count": 0}


async def _run_tool_loop(req: ChatRequest) -> dict:
    """One Hermes agent turn -> native OpenAI response."""
    messages = [m.model_dump() for m in req.messages]
    base_prompt = prompt_builder.build_prompt(messages, req.tools)
    ds_model = _model_for_mode(req.model)
    # Pull any base64 images from the latest user turn -> upload to vision.
    images = prompt_builder.extract_images(messages)
    if images:
        ds_model = "deepseek-vision"
        logger.info("attaching %d image(s) -> deepseek-vision", len(images))

    # --- Completion gate ---
    # DeepSeek loops after tool results. A cheap micro-question detects "done".
    # But it must be STRICT: a false YES ends the task early and the model
    # hallucinates a result. Only fire after several tool results, and require
    # the model to also confirm nothing is left undone.
    tool_results = [m for m in messages if m.get("role") == "tool"]
    last_role = messages[-1].get("role") if messages else None
    # Only gate when we are MID-LOOP: the last message must be a tool result.
    # If the last message is a fresh user request, the gate must NOT fire — old
    # tool results from a prior task would otherwise trigger a false "done" and
    # the model returns prose ("file updated") without ever calling a tool.
    if last_role == "tool" and len(tool_results) >= 3:
        gate_prompt = (base_prompt +
            "\n\nReview the ORIGINAL user request and ALL tool results above. "
            "Is EVERY part of the task actually finished, with no remaining step? "
            "Answer exactly one word: YES only if truly complete, otherwise NO.")
        try:
            verdict = (await bridge.complete(gate_prompt, model=ds_model)).strip().upper()
            if verdict.startswith("YES"):
                final_prompt = (base_prompt +
                    "\n\nThe task is complete. Give the user a short final answer in "
                    "plain text summarizing what was done. Do NOT call any tool.")
                ans = await bridge.complete(final_prompt, model=ds_model)
                tc, content, how = toolcall.parse(ans)
                if content:
                    logger.info("completion-gate: finalized after %d tool results", len(tool_results))
                    return _resp_envelope(req.model, content=content)
        except Exception as e:
            logger.warning("completion-gate skipped: %s", e)

    cur_prompt = base_prompt
    last_error = ""
    for attempt in range(settings.MAX_RECOVERY + 1):
        raws = await bridge.fan_out(cur_prompt, settings.VOTE_N, model=ds_model, images=images)
        decision = validator.vote(raws, req.tools)
        logger.info("turn attempt=%d kind=%s votes=%s/%s stats=%s",
                    attempt, decision["kind"], decision.get("votes"),
                    decision.get("total"), decision.get("parsed_stats"))

        if decision["kind"] == "tool_calls":
            # Enforce single-call-per-turn: DeepSeek can't reliably chain, so
            # keep only the first call and discard the rest (the next turn will
            # decide the follow-up once it has the tool result).
            calls = decision["tool_calls"][:1]
            reasoning = decision.get("content")  # Extract reasoning if present
            ok, err = validator.validate_tool_calls(calls, req.tools)
            if ok:
                tcs = await _heal_big_writes(calls, messages, ds_model)
                return _resp_envelope(req.model, content=reasoning,
                                      tool_calls=tcs)
            last_error = err
            cur_prompt = validator.recovery_prompt(base_prompt, err)
            continue
        # plain text answer
        if decision["content"]:
            # Anti-hallucination: DeepSeek thinks it IS the executor and reports
            # "file created / done" in prose without ever emitting a tool call.
            # Two cases to reject:
            #  (a) fresh user turn (no tool result) + claims done, OR
            #  (b) claims a file was written/created but write_file was NEVER
            #      actually called this whole session. Either way force a real
            #      tool call instead of a fake "done".
            claims = _claims_done(decision["content"])
            no_write = not _write_ever_called(messages)
            if claims and (last_role != "tool" or no_write):
                last_error = "claimed work done without any write tool call"
                cur_prompt = base_prompt + (
                    "\n\nYou claimed work is done but NO write/create tool was "
                    "ever called — the file does not exist yet. Do not say a file "
                    "was created. Emit the required write_file tool call now as JSON.")
                continue
            # Stall guard (semantic, not keyword): when tools exist but the
            # model returned prose, ASK it whether that prose was a final answer
            # or it intended to call a tool but forgot. One cheap yes/no. If it
            # was an intent, force the actual tool call. Only on attempt 0 so a
            # genuine final answer isn't blocked forever. Skip entirely when the
            # last message is a tool result: prose there is the finalization of
            # what the tool returned (e.g. summarizing a vision analysis), not a
            # stall — gating it loops the agent forever.
            if req.tools and attempt == 0 and last_role != "tool":
                check = (base_prompt +
                    "\n\nYour last draft was:\n\"\"\"\n" + decision["content"][:600] +
                    "\n\"\"\"\nWas this a COMPLETE final answer to the user, or were "
                    "you about to call a tool? Answer one word: FINAL or TOOL.")
                try:
                    verdict = (await bridge.complete(check, model=ds_model)).strip().upper()
                except Exception:
                    verdict = "FINAL"
                if verdict.startswith("TOOL"):
                    last_error = "intended a tool call but returned prose"
                    cur_prompt = base_prompt + (
                        "\n\nDo NOT describe what you will do. Emit the tool call as "
                        "JSON now: {\"tool_calls\":[{\"name\":...,\"arguments\":{...}}]}")
                    continue
            return _resp_envelope(req.model, content=decision["content"])
        last_error = "empty response"
        cur_prompt = validator.recovery_prompt(base_prompt, last_error)

    # all recoveries exhausted -> return whatever text we can, as content
    return _resp_envelope(req.model,
                          content=f"[orchestrator: failed to get valid tool call after "
                                  f"{settings.MAX_RECOVERY} recoveries: {last_error}]")


async def _run_deep(req: ChatRequest) -> dict:
    """Full pipeline for a single complex task."""
    # take the last user message as the task
    task = ""
    for m in reversed(req.messages):
        if m.role == "user":
            task = m.content if isinstance(m.content, str) else json.dumps(m.content)
            break
    plan = await planner.make_plan(task)
    logger.info("deep plan: %d steps", len(plan))
    results = await executor.execute(task, plan)
    final = await aggregator.aggregate(task, plan, results)
    return _resp_envelope(req.model, content=final)


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest,
                          x_orch_mode: Optional[str] = Header(default=None)):
    # DEBUG: Log what Hermes sends
    logger.info(f"[REQ] model={req.model}, user={getattr(req, 'user', '(no user field)')}, messages={len(req.messages)}")
    
    mode = (x_orch_mode or
            ("deep" if "-deep" in req.model or req.model.endswith("deep")
             else settings.DEFAULT_MODE))

    bh = await bridge.health()
    if not bh.get("extension_connected"):
        raise HTTPException(503, "DeepSeek Bridge has no Chrome extension connected")

    if mode == "deep":
        result = await _run_deep(req)
    elif mode == "persistent_chat":
        # Persistent chat mode - requires backend context API
        result = await _run_persistent_chat(req)
    else:
        # Default: tool_loop mode (transparent proxy)
        result = await _run_tool_loop(req)

    if req.stream:
        # minimal single-chunk SSE for clients that demand streaming
        def gen():
            choice = result["choices"][0]["message"]
            chunk = {
                "id": result["id"], "object": "chat.completion.chunk",
                "created": result["created"], "model": result["model"],
                "choices": [{"index": 0, "delta": choice,
                             "finish_reason": result["choices"][0]["finish_reason"]}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    return JSONResponse(result)


@app.on_event("shutdown")
async def _shutdown():
    await bridge.close()
