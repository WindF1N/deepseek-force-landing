"""Chat rotation and context summarization for persistent chats."""
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger("orchestrator.rotation")


async def rotate_chat(ctx, tools: List[Dict[str, Any]], bridge_client):
    """Rotate chat when context is full - create new chat with summary.
    
    Flow:
    1. Summarize current chat history
    2. Create new DeepSeek chat
    3. Initialize with tools + summary
    4. Replace primary_chat
    """
    import prompt as prompt_builder
    
    old_chat_id = ctx.primary_chat.chat_session_id
    old_token_count = ctx.primary_chat.token_count
    old_message_count = ctx.message_count
    
    logger.info(f"🔄 Rotating chat for user {ctx.user_id}: tokens={old_token_count}, messages={old_message_count}")
    
    # 1. Summarize current conversation
    summary_prompt = """Summarize this conversation in 500 words maximum.

Focus on:
- User's original goal and current objective
- All completed actions (files created, commands run, tools used)
- Current state and what's working
- Any pending tasks or next steps

Be concise but preserve all critical details, file paths, and decisions made."""
    
    try:
        summary_result = await ctx.primary_chat.send(summary_prompt)
        summary_text = summary_result.get("content", "")
        
        if not summary_text or len(summary_text) < 50:
            summary_text = f"Previous conversation had {ctx.message_count} messages."
            logger.warning(f"Summary too short, using fallback")
        
        ctx.context_summary = summary_text
        logger.info(f"✅ Generated summary: {len(summary_text)} chars")
        
    except Exception as e:
        logger.warning(f"⚠️  Summary generation failed: {e}, using fallback")
        ctx.context_summary = f"Continuing from previous session ({ctx.message_count} messages)."
    
    # 2. Close old chat
    await ctx.primary_chat.close()
    logger.debug(f"Closed old chat {old_chat_id}")
    
    # 3. Create new chat
    new_ctx = await bridge_client.get_or_create_context(f"{ctx.user_id}_rotated")
    
    # 4. Initialize new chat with summary
    init_with_summary = f"""You are an AI assistant with tool-calling abilities.

AVAILABLE TOOLS:
{_format_tools_simple(tools)}

RESPONSE PROTOCOL:
1. If you need to use a tool, respond with JSON:
   {{"tool_calls": [{{"name": "<tool_name>", "arguments": {{...}}}}]}}
2. If task is complete, respond in plain text.
3. NEVER claim work is done without calling the required tool.

PRIOR CONTEXT (from previous session):
{ctx.context_summary}

The user will continue from where they left off.
Respond "Ready" to confirm you understand."""
    
    await new_ctx.primary_chat.send(init_with_summary)
    
    # 5. Replace primary chat
    ctx.primary_chat = new_ctx.primary_chat
    ctx.message_count = 0
    
    logger.info(f"✅ Rotation complete: {old_chat_id} → {new_ctx.primary_chat.chat_session_id}")
    logger.info(f"   Summary: {len(ctx.context_summary)} chars, reset message count to 0")


def _format_tools_simple(tools: List[Dict[str, Any]]) -> str:
    """Simple tool list for rotation init (lighter than full format)."""
    if not tools:
        return ""
    
    lines = []
    for t in tools:
        fn = t.get("function", t)
        name = fn.get("name", "?")
        desc = fn.get("description", "").strip()
        lines.append(f"- {name}: {desc}")
    
    return "\n".join(lines)
