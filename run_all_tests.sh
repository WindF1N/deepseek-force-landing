#!/bin/bash
echo "=========================================="
echo "Running ALL tests"
echo "=========================================="

echo ""
echo "1. Orchestrator suite..."
cd orchestrator && ../venv/bin/python -m pytest tests/test_orchestrator.py -v --tb=short 2>&1 | tail -15

echo ""
echo "2. Persistent chat tests..."
cd .. && ./venv/bin/python test_persistent_chat.py 2>&1 | grep -E "PASS|FAIL|chars"

echo ""
echo "3. Reminders & rotation tests..."
./venv/bin/python test_reminders_rotation.py 2>&1 | grep -E "PASS|FAIL|quality"

echo ""
echo "=========================================="
echo "Test summary complete"
echo "=========================================="
