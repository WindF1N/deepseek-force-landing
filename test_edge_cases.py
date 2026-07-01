#!/usr/bin/env python3
"""Edge case testing for persistent chat."""
import asyncio
import sys

sys.path.insert(0, '/Users/vergilobj/deepseek-api/orchestrator')

from reminders import ReminderScheduler
import prompt as prompt_builder


async def test_edge_cases():
    """Test edge cases and boundary conditions."""
    print("="*60)
    print("EDGE CASE TESTING")
    print("="*60)
    
    issues = []
    
    # Test 1: Empty messages
    print("\n1. Empty messages handling")
    try:
        result = prompt_builder.build_current_turn([])
        if not result or result.strip() == "":
            print("   ⚠️  Empty messages return empty prompt (expected)")
        else:
            print(f"   ✅ Empty messages handled: '{result[:50]}'")
    except Exception as e:
        print(f"   ❌ ISSUE: Empty messages crash: {e}")
        issues.append("Empty messages crash")
    
    # Test 2: Missing role field
    print("\n2. Missing role field")
    try:
        bad_messages = [{"content": "test"}]
        result = prompt_builder.build_current_turn(bad_messages)
        print(f"   ✅ Missing role handled gracefully")
    except Exception as e:
        print(f"   ❌ ISSUE: Missing role crashes: {e}")
        issues.append("Missing role field crashes")
    
    # Test 3: Very long message
    print("\n3. Very long message handling")
    try:
        long_content = "test " * 50000  # ~250K chars
        messages = [{"role": "user", "content": long_content}]
        result = prompt_builder.build_current_turn(messages)
        print(f"   Result length: {len(result)} chars")
        if len(result) > 200000:
            print("   ⚠️  WARNING: Very long prompts not truncated")
            issues.append("Very long prompts not truncated")
        else:
            print(f"   ✅ Handled (truncated or within limits)")
    except Exception as e:
        print(f"   ❌ ISSUE: Long message crashes: {e}")
        issues.append("Long messages crash")
    
    # Test 4: Quality score edge cases
    print("\n4. Quality score boundaries")
    scheduler = ReminderScheduler()
    
    # Test lower bound
    for _ in range(20):
        scheduler.update_quality("failed")
    
    if scheduler.quality_score < 0:
        print(f"   ❌ ISSUE: Quality goes negative: {scheduler.quality_score}")
        issues.append("Quality score goes negative")
    else:
        print(f"   ✅ Quality bounded at {scheduler.quality_score:.2f}")
    
    # Test upper bound
    scheduler2 = ReminderScheduler()
    for _ in range(20):
        scheduler2.update_quality("clean")
    
    if scheduler2.quality_score > 1.0:
        print(f"   ❌ ISSUE: Quality exceeds 1.0: {scheduler2.quality_score}")
        issues.append("Quality score exceeds 1.0")
    else:
        print(f"   ✅ Quality capped at {scheduler2.quality_score:.2f}")
    
    # Test 5: Reminder text with empty list
    print("\n5. Empty reminder list")
    try:
        text = scheduler.get_reminder_text([])
        if text == "":
            print(f"   ✅ Empty list returns empty string")
        else:
            print(f"   ⚠️  Empty list returns: '{text[:50]}'")
    except Exception as e:
        print(f"   ❌ ISSUE: Empty list crashes: {e}")
        issues.append("Empty reminder list crashes")
    
    # Test 6: Unknown reminder type
    print("\n6. Unknown reminder type")
    try:
        text = scheduler.get_reminder_text(["nonexistent_reminder"])
        print(f"   ✅ Unknown reminder handled: '{text[:50] if text else 'empty'}'")
    except Exception as e:
        print(f"   ❌ ISSUE: Unknown reminder crashes: {e}")
        issues.append("Unknown reminder type crashes")
    
    # Test 7: Rapid quality changes
    print("\n7. Rapid quality oscillation")
    scheduler3 = ReminderScheduler()
    for i in range(20):
        scheduler3.update_quality("clean" if i % 2 == 0 else "failed")
    
    print(f"   Final quality after oscillation: {scheduler3.quality_score:.2f}")
    if 0.3 <= scheduler3.quality_score <= 0.7:
        print(f"   ✅ Stabilized in middle range")
    else:
        print(f"   ⚠️  Quality not stable: {scheduler3.quality_score}")
    
    # Test 8: Rotation trigger with edge values
    print("\n8. Rotation triggers")
    scheduler4 = ReminderScheduler()
    scheduler4.quality_score = 0.2  # Exactly at threshold
    
    if scheduler4.needs_rotation():
        print(f"   ❌ ISSUE: Triggers at threshold (should be <0.2)")
        issues.append("Rotation triggers at boundary")
    else:
        print(f"   ✅ Correct: doesn't trigger at 0.2")
    
    scheduler4.quality_score = 0.19
    if scheduler4.needs_rotation():
        print(f"   ✅ Triggers below threshold")
    else:
        print(f"   ❌ ISSUE: Doesn't trigger at 0.19")
        issues.append("Rotation doesn't trigger when needed")
    
    # Summary
    print("\n" + "="*60)
    if issues:
        print(f"❌ FOUND {len(issues)} ISSUES:")
        for i, issue in enumerate(issues, 1):
            print(f"   {i}. {issue}")
    else:
        print("✅ ALL EDGE CASES HANDLED CORRECTLY")
    print("="*60)
    
    return len(issues)


if __name__ == "__main__":
    issues_found = asyncio.run(test_edge_cases())
    sys.exit(issues_found)
