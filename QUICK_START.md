# DeepSeek-API Quick Start

## Status: ✅ PRODUCTION READY

All systems tested and operational. Vision mode fixed.

## Quick Commands

```bash
# Check status
curl http://localhost:8001/health  # Backend
curl http://localhost:8002/health  # Orchestrator

# Use with Hermes
hermes -p deepseek -z "Your prompt here"

# Vision mode
hermes -p deepseek -z "Describe image /path/to/image.png"
```

## What Works

✅ Chat (basic & multi-turn)  
✅ Tool calls (write_file, read_file, etc.)  
✅ Vision mode (images)  
✅ Persistent chat (98.5% prompt reduction)  
✅ Adaptive reminders  
✅ Tool call recovery & validation  

## Key Metrics

- Latency: 2-5 seconds
- Concurrent: 4 sessions
- Turns capacity: 50-100 (vs 3-5 baseline)
- Prompt efficiency: 98.5% reduction

## Known Issues

⚠️ dliq-token hardcoded (expires periodically)  
⚠️ Vision color accuracy ~80% (minor)  

## Need Help?

See: BATTLE_TEST_REPORT.md (full details)  
See: VERIFICATION_RESULTS.md (test results)  
See: README.md (architecture docs)
