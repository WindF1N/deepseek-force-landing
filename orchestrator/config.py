import os


class Settings:
    # --- Bridge (DeepSeek proxy) ---
    BRIDGE_URL: str = os.getenv("BRIDGE_URL", "http://localhost:8001")
    BRIDGE_COMPLETIONS: str = BRIDGE_URL + "/v1/chat/completions"
    BRIDGE_HEALTH: str = BRIDGE_URL + "/health"

    # --- Orchestrator server ---
    HOST: str = os.getenv("ORCH_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("ORCH_PORT", "8002"))
    API_KEY: str = os.getenv("ORCH_API_KEY", "supersecretkey")

    # --- Timeouts / limits ---
    BRIDGE_TIMEOUT: float = float(os.getenv("BRIDGE_TIMEOUT", "300"))
    MAX_PARALLEL_SESSIONS: int = int(os.getenv("MAX_PARALLEL_SESSIONS", "5"))

    # --- Self-consistency voting (the lever that makes a weaker model sturdier) ---
    # On each agent turn we fire N parallel DeepSeek calls and vote on the result.
    # 1 = disabled (single call). 3 is a good default if you have >=3 Chrome windows.
    VOTE_N: int = int(os.getenv("VOTE_N", "1"))

    # --- Recovery ---
    MAX_RECOVERY: int = int(os.getenv("MAX_RECOVERY", "3"))

    # --- Pipeline mode ---
    # "tool_loop"  : transparent OpenAI proxy for Hermes agent loop (stable)
    # "persistent_chat" : reuse DeepSeek chat context, minimal prompts (NEW - testing!)
    # "deep"       : full Planner->Executor->Validator->Aggregator for one-shot complex tasks
    DEFAULT_MODE: str = os.getenv("ORCH_MODE", "persistent_chat")

    # Planner only kicks in for prompts longer than this many chars (deep mode heuristic)
    PLANNER_MIN_CHARS: int = int(os.getenv("PLANNER_MIN_CHARS", "400"))


settings = Settings()
