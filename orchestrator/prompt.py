"""Render a Hermes-style OpenAI request into a single text prompt for DeepSeek.

Hermes sends, every turn:
  - a big system prompt
  - a list of tool schemas (JSON-Schema function defs)
  - the full message history (user / assistant / tool results)

DeepSeek-web takes one flat prompt and answers with text. We linearize the
whole thing and append a strict instruction telling DeepSeek to either call a
tool (as JSON) or answer in plain text.
"""
import json
import re
from typing import List, Dict, Any, Optional


TOOL_INSTRUCTION = (
    "\n\n========================================\n"
    "RESPONSE PROTOCOL (read carefully):\n"
    "You are the reasoning engine for an agent that can call tools.\n"
    "1. If you need to use a tool, respond with ONE single JSON object and "
    "NOTHING else (no prose, no markdown fences, no ```json wrapper):\n"
    '   {"tool_calls": [{"name": "<tool_name>", "arguments": {<json args>}}]}\n'
    "   CRITICAL: Output ONLY the raw JSON starting with { and ending with }.\n"
    "   DO NOT wrap in ```json or ``` markdown code blocks!\n"
    "   Call EXACTLY ONE tool per turn. Never chain calls. Wait for the tool "
    "result before deciding the next step. Do NOT guess arguments that depend "
    "on a tool you have not run yet.\n"
    "2. If the task is complete, or you are answering the user directly, or "
    "summarizing tool results, respond in PLAIN TEXT with no JSON.\n"
    "3. Never invent a tool that is not in the AVAILABLE TOOLS list.\n"
    "4. Arguments must match the tool's parameter schema exactly.\n"
    "5. CRITICAL: You have NO ability to act on your own. You have no file "
    "system, no shell, no network. The ONLY way anything happens is a tool "
    "call. NEVER claim a file was created/edited, a command ran, or a result "
    "exists unless a tool RESULT for it appears above. If the work is not yet "
    "done, emit the tool call — do not describe it as finished.\n"
    "========================================\n"
)

# Short reminder prepended to EVERY user message (critical for format compliance)
TOOL_CALL_REMINDER = (
    "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "⚠️ ЗАПОМНИ: Если тебе нужно вызвать какой-то инструмент то ИСПОЛЬЗУЙ ТОЛЬКО ЭТОТ ФОРМАТ (В САМОМ НАЧАЛЕ ДИАЛОГА УПОМИНАЛ):\n"
    '{"tool_calls": [{"name": "tool_name", "arguments": {...}}]}\n'
    "НИКАКИХ XML! НИКАКИХ markdown! НИКАКИХ <function_calls>! ТОЛЬКО формат JSON.\n"
    "НЕ ОБОРАЧИВАЙ в ```json или ``` — только чистый JSON начиная с { и заканчивая }!\n"
    "⚠️ ИСПОЛЬЗУЙ ТОЧНЫЕ ИМЕНА и ТОЧНЫЕ АРГУМЕНТЫ из списка AVAILABLE TOOLS!\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
)


def _fmt_tools(tools: Optional[List[Dict[str, Any]]]) -> str:
    if not tools:
        return ""
    lines = ["AVAILABLE TOOLS:"]
    for t in tools:
        fn = t.get("function", t)
        name = fn.get("name", "?")
        desc = (fn.get("description", "") or "").strip()
        params = fn.get("parameters", {})
        lines.append(f"\n- {name}: {desc}")
        props = params.get("properties", {}) if isinstance(params, dict) else {}
        required = set(params.get("required", []) if isinstance(params, dict) else [])
        for pname, pinfo in props.items():
            ptype = pinfo.get("type", "any") if isinstance(pinfo, dict) else "any"
            pdesc = (pinfo.get("description", "") if isinstance(pinfo, dict) else "") or ""
            req = " (required)" if pname in required else ""
            lines.append(f"    • {pname} [{ptype}]{req}: {pdesc}")
    return "\n".join(lines)


_DATA_URI_RE = re.compile(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", re.I)
_MAX_TOOL_CHARS = 8000          # screenshots/blobs in tool results blow the prompt


def _scrub(text: str) -> str:
    """Strip base64 image blobs from tool results; cap giant outputs."""
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    # NOTE: Don't strip images here - they're handled separately via images[] param
    # text = _DATA_URI_RE.sub("[image omitted]", text)
    if len(text) > _MAX_TOOL_CHARS:
        text = text[:_MAX_TOOL_CHARS] + "\n…[truncated]"
    return text


def _content_text(content: Any) -> str:
    """Flatten OpenAI multimodal content; images handled separately."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    out.append(part.get("text", ""))
                elif part.get("type") in ("image_url", "image"):
                    out.append("[image attached]")
            else:
                out.append(str(part))
        return "\n".join(out)
    return json.dumps(content, ensure_ascii=False)


def _fmt_messages(messages: List[Dict[str, Any]]) -> str:
    parts = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "system":
            parts.append(f"# SYSTEM INSTRUCTIONS\n{_scrub(_content_text(content))}")
        elif role == "user":
            parts.append(f"# USER\n{_scrub(_content_text(content))}")
        elif role == "assistant":
            tcs = msg.get("tool_calls")
            if tcs:
                rendered = []
                for tc in tcs:
                    fn = tc.get("function", {})
                    rendered.append(f'{fn.get("name")}({fn.get("arguments")})')
                parts.append("# ASSISTANT (called tools)\n" + "\n".join(rendered))
            elif content:
                parts.append(f"# ASSISTANT\n{_scrub(_content_text(content))}")
        elif role == "tool":
            tid = msg.get("tool_call_id", "")
            parts.append(f"# TOOL RESULT (for {tid})\n{_scrub(_content_text(content))}")
    return "\n\n".join(parts)


def build_prompt(messages: List[Dict[str, Any]],
                 tools: Optional[List[Dict[str, Any]]] = None) -> str:
    """Build the single flat prompt sent to DeepSeek for one agent turn."""
    sections = []
    body = _fmt_messages(messages)
    if body:
        sections.append(body)
    tools_block = _fmt_tools(tools)
    if tools_block:
        sections.append(tools_block)
    sections.append(TOOL_INSTRUCTION if tools else
                    "\nRespond directly to the user in plain text.")
    return "\n\n".join(sections)


_FULL_DATA_URI_RE = re.compile(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", re.I)


def extract_images(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Pull every base64 image out of the message history so the bridge can
    upload them to deepseek-vision. Looks in both flat string content and
    OpenAI multimodal parts. Returns [{base64, name}, ...] for the LATEST user
    turn only (we don't re-upload old screenshots every step)."""
    imgs: List[Dict[str, str]] = []
    last_user = None
    last_tool = None
    for m in messages:
        if m.get("role") == "user":
            last_user = m
        elif m.get("role") == "tool":
            last_tool = m
    targets = [t for t in (last_user, last_tool) if t]
    for msg in targets:
        c = msg.get("content")
        if isinstance(c, str):
            for i, uri in enumerate(_FULL_DATA_URI_RE.findall(c)):
                imgs.append({"base64": uri, "name": f"img_{i}.png"})
        elif isinstance(c, list):
            for i, part in enumerate(c):
                if isinstance(part, dict) and part.get("type") in ("image_url", "image"):
                    u = part.get("image_url", {})
                    url = u.get("url") if isinstance(u, dict) else u
                    if isinstance(url, str) and url.startswith("data:image"):
                        imgs.append({"base64": url, "name": f"img_{i}.png"})
    return imgs


# ============================================================================
# PERSISTENT CHAT: System Init + Current Turn
# ============================================================================

def build_system_init(messages: List[Dict[str, Any]],
                      tools: Optional[List[Dict[str, Any]]] = None) -> str:
    """Build initialization prompt sent ONCE when creating a persistent chat.
    
    Includes:
    - System instructions (if present)
    - Full tools list
    - Format rules
    """
    sections = []
    
    # System message (if present)
    system_msg = next((m for m in messages if m.get("role") == "system"), None)
    if system_msg:
        content = _content_text(system_msg.get("content"))
        sections.append(f"# SYSTEM INSTRUCTIONS\n{_scrub(content)}")
    
    # Tools
    tools_block = _fmt_tools(tools)
    if tools_block:
        sections.append(tools_block)
    
    # Response protocol
    sections.append(TOOL_INSTRUCTION if tools else
                    "\nRespond directly to the user in plain text.")
    
    # Confirmation request
    sections.append('\nThis is a persistent conversation. '
                    'Future messages will reference this context.\n'
                    'Respond "Ready" to confirm you understand.')
    
    return "\n\n".join(sections)


def build_current_turn(messages: List[Dict[str, Any]],
                       add_reminder: str = None) -> str:
    """Build prompt for current turn in persistent chat.
    
    Only includes:
    - Latest user message
    - Recent tool results
    - Optional reminder about format
    
    Does NOT include system/tools (already in chat context).
    """
    # Find last N messages (up to previous user message)
    recent = []
    user_count = 0
    
    for m in reversed(messages):
        recent.insert(0, m)
        if m.get("role") == "user":
            user_count += 1
            if user_count == 2:
                break  # Stop at previous user message
    
    # Format messages
    parts = []
    for msg in recent:
        role = msg.get("role")
        content = msg.get("content")
        
        if role == "user":
            parts.append(f"USER REQUEST:\n{_scrub(_content_text(content))}")
        elif role == "tool":
            tool_name = msg.get("name", "unknown")
            parts.append(f"TOOL RESULT ({tool_name}):\n{_scrub(_content_text(content))}")
        elif role == "assistant" and msg.get("tool_calls"):
            # Skip assistant tool_calls - already in DeepSeek history
            pass
    
    prompt = "\n\n".join(parts)
    
    # Add optional reminder
    if add_reminder:
        prompt += f"\n\n{add_reminder}"
    
    return prompt
