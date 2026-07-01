from fastapi import FastAPI, WebSocket, HTTPException, Request
from fastapi.responses import StreamingResponse
import asyncio
import logging
import uuid
import time
import json
from typing import List, Optional, Union
from pydantic import BaseModel, Field

from ws_server import extension_manager, chat_context_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# Models
# ------------------------------------------------------------
class ChatMessage(BaseModel):
    role: str
    content: Union[str, List[dict], None] = None
    tool_calls: Optional[List[dict]] = None
    tool_call_id: Optional[str] = None

class ChatCompletionRequest(BaseModel):
    model: str = "deepseek-chat"
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 500
    stream: Optional[bool] = False
    tools: Optional[List[dict]] = None
    tool_choice: Optional[str] = None
    # explicit per-request mode overrides (optional; otherwise inferred from model name)
    thinking: Optional[bool] = None
    search: Optional[bool] = None
    expert: Optional[bool] = None
    debug_raw: Optional[bool] = False
    pre_instruction: Optional[bool] = False
    images: Optional[List[dict]] = None

class ChatCompletionResponseChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str = "stop"

class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionResponseChoice]

# ------------------------------------------------------------
# FastAPI app
# ------------------------------------------------------------
app = FastAPI()


# Start TTL cleanup on startup
@app.on_event("startup")
async def startup_event():
    """Start background tasks."""
    chat_context_manager.start_cleanup()
    logger.info("Started chat context TTL cleanup task")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    key = websocket.query_params.get("key", None)
    session_id = websocket.query_params.get("session_id", None)
    await extension_manager.connect(websocket, key, session_id)

@app.get("/health")
async def health():
    connected = extension_manager.get_connection_count() > 0
    return {"status": "ok", "extension_connected": connected}


@app.get("/api/contexts/{user_id}")
async def get_context_info(user_id: str):
    """Get persistent chat context info for a user (without creating if doesn't exist)."""
    if user_id not in chat_context_manager.contexts:
        # Chat doesn't exist yet - return empty state
        return {
            "user_id": user_id,
            "message_count": 0,
            "chat_id": None,
            "token_count": 0,
        }
    
    ctx = chat_context_manager.contexts[user_id]
    return {
        "user_id": user_id,
        "message_count": ctx.message_count,
        "chat_id": ctx.primary_chat.chat_session_id if ctx.primary_chat else None,
        "token_count": ctx.primary_chat.token_count if ctx.primary_chat else 0,
    }


@app.post("/api/contexts/{user_id}/send")
async def send_to_persistent_chat(user_id: str, request: Request):
    """Send a message to user's persistent DeepSeek chat and get response."""
    payload = await request.json()
    prompt = payload.get("prompt")
    model = payload.get("model", "deepseek-chat")
    images = payload.get("images")
    thinking_enabled = payload.get("thinking_enabled", False)  # Default False (deepthink disabled)
    search_enabled = payload.get("search_enabled", False)
    
    logger.info(f"[DEBUG] /api/contexts/{user_id}/send: model={model}")
    
    if not prompt:
        raise HTTPException(400, "prompt required")
    
    ctx = await chat_context_manager.get_or_create(user_id)
    
    # Map model name to model_type for DeepSeek API
    # DeepSeek supports: None (default chat), "expert" (reasoner), "vision"
    model_type = None
    if model == "deepseek-vision" or "vision" in model:
        model_type = "vision"
    elif model == "deepseek-expert" or "expert" in model:
        model_type = "expert"
    # deepseek-chat → model_type=None (default chat)
    
    logger.info(f"[DEBUG] Determined model_type={model_type}")
    
    # Send to persistent chat
    result = await ctx.primary_chat.send(
        prompt=prompt,
        model_type=model_type,
        thinking_enabled=thinking_enabled,
        search_enabled=search_enabled,
        images=images
    )
    
    if not result.get("success"):
        raise HTTPException(502, result.get("error", "Chat send failed"))
    
    # Increment message count
    ctx.message_count += 1
    
    return {
        "success": True,
        "content": result.get("content"),
        "thinking": result.get("thinking"),
        "message_id": ctx.primary_chat.parent_message_id,
        "token_count": ctx.primary_chat.token_count,
        "message_count": ctx.message_count
    }


# ------------------------------------------------------------
# Model metadata
# ------------------------------------------------------------
FULL_MODEL_DATA = {
    "deepseek-chat": {
        "id": "deepseek-chat",
        "object": "model",
        "created": 1700000000,
        "owned_by": "deepseek",
        "features": {"tool_use": True},
        "supported_parameters": ["tools", "tool_choice"],
    },
    "deepseek-expert": {
        "id": "deepseek-expert",
        "object": "model",
        "created": 1700000000,
        "owned_by": "deepseek",
        "features": {"tool_use": True},
        "supported_parameters": ["tools", "tool_choice"],
    },
    "deepseek-vision": {
        "id": "deepseek-vision",
        "object": "model",
        "created": 1700000000,
        "owned_by": "deepseek",
        "features": {"tool_use": True},
        "supported_parameters": ["tools", "tool_choice"],
    }
}

@app.get("/v1/models")
async def list_models_v1():
    return {"object": "list", "data": list(FULL_MODEL_DATA.values())}

@app.get("/models")
async def list_models():
    return await list_models_v1()

@app.get("/v1/models/{model_id}")
async def get_model(model_id: str):
    default = {
        "id": model_id,
        "object": "model",
        "created": 1700000000,
        "owned_by": "deepseek"
    }
    return FULL_MODEL_DATA.get(model_id, default)

# ------------------------------------------------------------
# Compatibility stubs
# ------------------------------------------------------------
@app.get("/api/v1/models")
async def api_models():
    return await list_models_v1()

@app.get("/api/tags")
async def api_tags():
    return {"models": []}

@app.get("/v1/props")
async def props():
    return {}

@app.get("/props")
async def props2():
    return {}

@app.get("/version")
async def version():
    return {"version": "1.0.0"}

@app.post("/api/show")
async def api_show():
    return {}

# ------------------------------------------------------------
# Main endpoint — proxy
# ------------------------------------------------------------
@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: ChatCompletionRequest):
    logger.info("Received request for model: %s", request.model)

    # Note: we no longer reject when all sessions are busy — the pool now
    # queues the request until a session frees up. Only a total absence of
    # connected extensions is a hard 503 (raised from send_request).

    # Build prompt from messages
    conversation_parts = []
    for msg in request.messages:
        if msg.role == "system":
            conversation_parts.append(f"System: {msg.content}")
        elif msg.role == "user":
            text = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
            conversation_parts.append(f"User: {text}")
        elif msg.role == "assistant":
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    conversation_parts.append(f"Assistant called tool: {tc['function']['name']}({tc['function']['arguments']})")
            elif msg.content:
                conversation_parts.append(f"Assistant: {msg.content}")
        elif msg.role == "tool":
            conversation_parts.append(f"Tool result (id={msg.tool_call_id}): {msg.content}")

    full_prompt = "\n\n".join(conversation_parts)

    # Determine model mode. Explicit request fields win; otherwise infer from
    # the model name (back-compat for "expert"/"vision"/"search" in the name).
    model = request.model
    logger.info(f"[DEBUG] Received model name: {model}")
    
    if request.expert is True or "expert" in model:
        model_type = "expert"
    elif "vision" in model:
        model_type = None
    else:
        model_type = None

    logger.info(f"[DEBUG] Determined model_type: {model_type}")
    
    if request.search is not None:
        search_enabled = request.search
    else:
        search_enabled = "search" in model

    if request.thinking is not None:
        thinking_enabled = request.thinking
    else:
        thinking_enabled = "think" in model

    # If images are attached, force vision mode so DeepSeek actually sees them.
    has_images = bool(request.images)
    payload = {
        "prompt": full_prompt,
        "model_type": model_type,
        "search_enabled": search_enabled,
        "thinking_enabled": False,  # deepthink disabled (slow + tool calling conflicts)
        "debug_raw": bool(request.debug_raw),
        "pre_instruction": bool(request.pre_instruction),
        "images": request.images or [],
    }
    if has_images:
        payload["model_type"] = "vision"  # web vision needs explicit vision mode
        payload["search_enabled"] = False
        payload["thinking_enabled"] = False  # deepthink off for vision

    logger.info("SEND model=%s images=%d prompt_len=%d model_type=%s search=%s think=%s",
                model, len(request.images or []), len(full_prompt),
                payload["model_type"], payload["search_enabled"], False)  # thinking always False
    
    # DEBUG: Save full prompt to file
    import datetime
    debug_file = f"/tmp/hermes_prompt_{datetime.datetime.now().strftime('%H%M%S')}.txt"
    try:
        with open(debug_file, 'w', encoding='utf-8') as f:
            f.write(f"=== HERMES FULL PROMPT ({len(full_prompt)} chars) ===\n\n")
            f.write(full_prompt)
            f.write("\n\n=== END ===")
        logger.info(f"💾 Saved full prompt to {debug_file}")
    except Exception as e:
        logger.warning(f"Failed to save debug prompt: {e}")

    # Retry on empty/failed DeepSeek responses (expired PoW, empty SSE, refusals).
    # Each retry grabs a fresh session from the pool and a fresh PoW in the
    # extension, which clears most transient empties.
    last_error = "Internal error"
    result = None
    for attempt in range(3):
        try:
            result = await extension_manager.send_request(payload)
        except RuntimeError as e:
            # "No extension connected" -> 503; queue timeout -> retry briefly
            msg = str(e)
            if "No extension connected" in msg:
                raise HTTPException(status_code=503, detail=msg)
            last_error = msg
            logger.warning("attempt %d RuntimeError: %s", attempt, msg)
            await asyncio.sleep(0.5 * (attempt + 1))
            continue

        # full extension result so vision/upload failures are visible
        logger.info("attempt %d RESULT: success=%s err=%s content_len=%d refFiles=%s keys=%s",
                    attempt, result.get("success"), result.get("error"),
                    len(result.get("content", "") or ""), result.get("refFiles"), list(result.keys()))
        if result.get("success") and result.get("content", "").strip():
            break
        last_error = result.get("error", "Empty response from DeepSeek")
        logger.warning("attempt %d failed: %s", attempt, last_error)
        await asyncio.sleep(0.5 * (attempt + 1))

    if result is None or not result.get("success") or not result.get("content", "").strip():
        raise HTTPException(status_code=502, detail=f"DeepSeek failed after retries: {last_error}")

    content = result["content"]
    logger.info("DeepSeek response: %s...", content[:200])

    # Streaming or regular response
    if request.stream:
        async def event_stream():
            chunk = {
                "id": f"chatcmpl-{uuid.uuid4()}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": request.model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": content},
                    "finish_reason": "stop"
                }]
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: \n\n"
        return StreamingResponse(event_stream(), media_type="text/event-stream")
    else:
        message = {"role": "assistant", "content": content}
        # surface reasoning + search citations as extra fields for the orchestrator
        if result.get("thinking"):
            message["reasoning_content"] = result["thinking"]
        if result.get("citations"):
            message["citations"] = result["citations"]
        return {
            "id": f"chatcmpl-{uuid.uuid4()}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model,
            "choices": [
                {"index": 0, "message": message, "finish_reason": "stop"}
            ],
        }