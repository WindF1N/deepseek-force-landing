#!/usr/bin/env python3
"""Performance testing for persistent chat."""
import asyncio
import sys
import time

sys.path.insert(0, '/Users/vergilobj/deepseek-api/orchestrator')

from reminders import ReminderScheduler
import prompt as prompt_builder


async def test_performance():
    """Test performance of key operations."""
    print("="*60)
    print("PERFORMANCE TESTING")
    print("="*60)
    
    # Test 1: Prompt building speed
    print("\n1. Prompt building speed")
    messages = [
        {"role": "system", "content": "You are helpful"},
        {"role": "user", "content": "Test " * 100},
        {"role": "assistant", "tool_calls": [{"function": {"name": "test"}}]},
        {"role": "tool", "name": "test", "content": "Result"},
    ]
    tools = [
        {"function": {"name": f"tool_{i}", "description": f"Tool {i}"}}
        for i in range(50)  # 50 tools
    ]
    
    start = time.time()
    for _ in range(100):
        _ = prompt_builder.build_system_init(messages, tools)
    elapsed = time.time() - start
    
    print(f"   100 system_init builds: {elapsed*1000:.2f}ms")
    print(f"   Per call: {elapsed*10:.2f}ms")
    
    if elapsed > 1.0:
        print(f"   ⚠️  Slow: {elapsed:.2f}s for 100 calls")
    else:
        print(f"   ✅ Fast enough")
    
    start = time.time()
    for _ in range(1000):
        _ = prompt_builder.build_current_turn(messages)
    elapsed = time.time() - start
    
    print(f"   1000 current_turn builds: {elapsed*1000:.2f}ms")
    print(f"   Per call: {elapsed:.2f}ms")
    
    if elapsed > 1.0:
        print(f"   ⚠️  Slow: {elapsed:.2f}s for 1000 calls")
    else:
        print(f"   ✅ Fast enough")
    
    # Test 2: Reminder scheduler speed
    print("\n2. Reminder scheduler operations")
    scheduler = ReminderScheduler()
    
    start = time.time()
    for i in range(1000):
        scheduler.update_quality("clean" if i % 2 == 0 else "salvaged")
    elapsed = time.time() - start
    
    print(f"   1000 quality updates: {elapsed*1000:.2f}ms")
    print(f"   Per update: {elapsed:.3f}ms")
    
    if elapsed > 0.5:
        print(f"   ⚠️  Slow: {elapsed:.2f}s")
    else:
        print(f"   ✅ Fast")
    
    start = time.time()
    for _ in range(1000):
        _ = scheduler.should_remind("normal")
    elapsed = time.time() - start
    
    print(f"   1000 reminder checks: {elapsed*1000:.2f}ms")
    
    if elapsed > 0.5:
        print(f"   ⚠️  Slow: {elapsed:.2f}s")
    else:
        print(f"   ✅ Fast")
    
    # Test 3: Memory usage estimate
    print("\n3. Memory footprint")
    import sys
    
    scheduler_size = sys.getsizeof(scheduler)
    print(f"   ReminderScheduler: {scheduler_size} bytes")
    
    big_prompt = prompt_builder.build_system_init(messages * 10, tools * 2)
    prompt_size = sys.getsizeof(big_prompt)
    print(f"   Large prompt: {len(big_prompt)} chars, {prompt_size} bytes")
    
    if prompt_size > 1_000_000:
        print(f"   ⚠️  Large: {prompt_size / 1024:.1f} KB")
    else:
        print(f"   ✅ Reasonable")
    
    # Summary
    print("\n" + "="*60)
    print("✅ PERFORMANCE TESTS COMPLETE")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(test_performance())
