#!/usr/bin/env python3
"""Error handling and safety testing."""
import asyncio
import sys

sys.path.insert(0, '/Users/vergilobj/deepseek-api/orchestrator')

from reminders import ReminderScheduler, REMINDERS
import rotation


async def test_error_handling():
    """Test error handling and safety."""
    print("="*60)
    print("ERROR HANDLING & SAFETY TESTING")
    print("="*60)
    
    issues = []
    
    # Test 1: None values
    print("\n1. None value handling")
    scheduler = ReminderScheduler()
    
    try:
        scheduler.update_quality(None)
        print(f"   Quality after None: {scheduler.quality_score:.2f}")
        if scheduler.quality_score < 0 or scheduler.quality_score > 1:
            issues.append("None quality leads to invalid score")
            print(f"   ❌ Invalid quality: {scheduler.quality_score}")
        else:
            print(f"   ✅ Handled gracefully")
    except Exception as e:
        print(f"   ❌ ISSUE: None crashes: {e}")
        issues.append(f"None quality crashes: {e}")
    
    # Test 2: Invalid event types
    print("\n2. Invalid event types")
    try:
        reminders = scheduler.should_remind(None)
        print(f"   Reminders for None: {reminders}")
        print(f"   ✅ None event handled")
    except Exception as e:
        print(f"   ❌ ISSUE: None event crashes: {e}")
        issues.append(f"None event crashes: {e}")
    
    try:
        reminders = scheduler.should_remind("invalid_event_12345")
        print(f"   Reminders for invalid: {reminders}")
        print(f"   ✅ Invalid event handled")
    except Exception as e:
        print(f"   ❌ ISSUE: Invalid event crashes: {e}")
        issues.append(f"Invalid event crashes: {e}")
    
    # Test 3: Extreme values
    print("\n3. Extreme values")
    scheduler2 = ReminderScheduler()
    scheduler2.quality_score = 999.9
    scheduler2.error_count = 10000
    scheduler2.consecutive_errors = 10000
    
    try:
        if scheduler2.needs_rotation():
            print(f"   ✅ Extreme values trigger rotation")
        else:
            print(f"   ⚠️  Extreme values don't trigger rotation")
    except Exception as e:
        print(f"   ❌ ISSUE: Extreme values crash: {e}")
        issues.append(f"Extreme values crash: {e}")
    
    # Test 4: Concurrent access simulation
    print("\n4. Rapid successive calls (concurrency sim)")
    scheduler3 = ReminderScheduler()
    
    try:
        for i in range(100):
            scheduler3.update_quality("clean")
            scheduler3.should_remind("normal")
            scheduler3.get_reminder_text(["format_light"])
        
        print(f"   ✅ 100 rapid calls succeeded")
        print(f"   Final quality: {scheduler3.quality_score:.2f}")
    except Exception as e:
        print(f"   ❌ ISSUE: Rapid calls crash: {e}")
        issues.append(f"Rapid calls crash: {e}")
    
    # Test 5: Rotation with empty/invalid tools
    print("\n5. Rotation with edge case inputs")
    try:
        result = rotation._format_tools_simple(None)
        print(f"   None tools: '{result}'")
        print(f"   ✅ None tools handled")
    except Exception as e:
        print(f"   ❌ ISSUE: None tools crash: {e}")
        issues.append(f"None tools crash: {e}")
    
    try:
        result = rotation._format_tools_simple([])
        print(f"   Empty tools: '{result}'")
        print(f"   ✅ Empty tools handled")
    except Exception as e:
        print(f"   ❌ ISSUE: Empty tools crash: {e}")
        issues.append(f"Empty tools crash: {e}")
    
    try:
        bad_tools = [{"bad": "structure"}]
        result = rotation._format_tools_simple(bad_tools)
        print(f"   Bad structure: '{result[:50] if result else 'empty'}'")
        print(f"   ✅ Bad structure handled")
    except Exception as e:
        print(f"   ❌ ISSUE: Bad tool structure crashes: {e}")
        issues.append(f"Bad tool structure crashes: {e}")
    
    # Test 6: Reminder text with None
    print("\n6. Reminder text safety")
    try:
        text = scheduler.get_reminder_text(None)
        print(f"   None list: '{text}'")
        print(f"   ✅ None list handled")
    except Exception as e:
        print(f"   ❌ ISSUE: None reminder list crashes: {e}")
        issues.append(f"None reminder list crashes: {e}")
    
    # Test 7: Integer overflow simulation
    print("\n7. Integer overflow protection")
    scheduler4 = ReminderScheduler()
    scheduler4.message_count = 2**31 - 1  # Max 32-bit int
    
    try:
        reminders = scheduler4.should_remind("normal")
        scheduler4.message_count += 1
        print(f"   Message count after overflow: {scheduler4.message_count}")
        print(f"   ✅ Large integers handled")
    except Exception as e:
        print(f"   ❌ ISSUE: Integer overflow: {e}")
        issues.append(f"Integer overflow: {e}")
    
    # Summary
    print("\n" + "="*60)
    if issues:
        print(f"❌ FOUND {len(issues)} SAFETY ISSUES:")
        for i, issue in enumerate(issues, 1):
            print(f"   {i}. {issue}")
    else:
        print("✅ ALL ERROR HANDLING TESTS PASSED")
    print("="*60)
    
    return len(issues)


if __name__ == "__main__":
    issues = asyncio.run(test_error_handling())
    sys.exit(issues)
