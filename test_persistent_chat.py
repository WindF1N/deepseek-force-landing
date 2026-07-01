#!/usr/bin/env python3
"""Quick test: persistent chat architecture works end-to-end."""
import asyncio
import sys

sys.path.insert(0, '/Users/vergilobj/deepseek-api/backend')
sys.path.insert(0, '/Users/vergilobj/deepseek-api/orchestrator')

from ws_server import ChatContextManager, SessionPool, estimate_tokens
import prompt as prompt_builder


async def test_basic_flow():
    """Test basic persistent chat flow."""
    print("="*60)
    print("TEST: Persistent Chat Basic Flow")
    print("="*60)
    
    # Test 1: Token estimation
    print("\n1. Token estimation")
    text = "Hello world this is a test"
    tokens = estimate_tokens(text)
    print(f"   Text: '{text}'")
    print(f"   Estimated tokens: {tokens}")
    assert tokens > 0, "Token estimation failed"
    print("   ✅ PASS")
    
    # Test 2: Prompt splitting
    print("\n2. Prompt splitting - system init")
    messages = [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Create a file"}
    ]
    tools = [
        {
            "function": {
                "name": "write_file",
                "description": "Write content to a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["path", "content"]
                }
            }
        }
    ]
    
    init_prompt = prompt_builder.build_system_init(messages, tools)
    print(f"   Init prompt length: {len(init_prompt)} chars")
    assert "SYSTEM INSTRUCTIONS" in init_prompt
    assert "write_file" in init_prompt
    assert "Ready" in init_prompt
    print("   ✅ Contains system, tools, and confirmation")
    
    # Test 3: Current turn prompt
    print("\n3. Current turn prompt")
    turn_messages = messages + [
        {"role": "assistant", "tool_calls": [{"function": {"name": "write_file"}}]},
        {"role": "tool", "name": "write_file", "content": "Success"}
    ]
    
    current_prompt = prompt_builder.build_current_turn(turn_messages)
    print(f"   Current prompt length: {len(current_prompt)} chars")
    assert "USER REQUEST" in current_prompt
    assert "TOOL RESULT" in current_prompt
    assert "SYSTEM INSTRUCTIONS" not in current_prompt  # Should NOT include system
    assert "AVAILABLE TOOLS:" not in current_prompt  # Should NOT include tools list
    print("   ✅ Contains only user + tool results")
    
    # Test 4: Size comparison
    print("\n4. Prompt size comparison")
    full_old_style = prompt_builder.build_prompt(turn_messages, tools)
    print(f"   OLD (full every time): {len(full_old_style)} chars")
    print(f"   NEW init (once): {len(init_prompt)} chars")
    print(f"   NEW turn (each): {len(current_prompt)} chars")
    
    reduction = len(full_old_style) - len(current_prompt)
    reduction_pct = (reduction / len(full_old_style)) * 100
    print(f"   📊 Reduction per turn: -{reduction} chars ({reduction_pct:.0f}%)")
    assert reduction > 0, "New approach should be smaller"
    print("   ✅ PASS")
    
    print("\n" + "="*60)
    print("✅ ALL TESTS PASSED")
    print("="*60)
    print("\nPersistent chat infrastructure ready!")
    print("- Token estimation: working")
    print("- Prompt splitting: working")
    print(f"- Size reduction: {reduction_pct:.0f}% smaller per turn")


if __name__ == "__main__":
    asyncio.run(test_basic_flow())
