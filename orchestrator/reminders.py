"""Adaptive reminders to keep DeepSeek on format."""
import logging
from typing import List

logger = logging.getLogger("orchestrator.reminders")


# Reminder templates
REMINDERS = {
    "format_light": "\n[Respond with tool call JSON or final text answer]",
    
    "format_strict": """
⚠️ FORMAT REMINDER:
Tool call: {"tool_calls": [{"name": "exact_name", "arguments": {...}}]}
Final answer: Plain text (no JSON)
Choose ONE. No markdown blocks.""",
    
    "anti_hallucination": """
⚠️ CRITICAL: You cannot act directly. Only via tool calls.
Do not claim "file created" or "done" unless tool result confirms it.""",
    
    "completion_check": """
Before saying "done", verify ALL parts of user's request are complete.
If anything is missing → emit the required tool call.""",
    
    "after_error": """
⚠️ Your previous response could not be parsed.
Emit ONLY valid JSON for tool calls:
{"tool_calls": [{"name": "tool_name", "arguments": {...}}]}
Or plain text for final answer. No mixing."""
}


class ReminderScheduler:
    """Adaptive reminder system - adjusts frequency based on quality."""
    
    def __init__(self):
        self.message_count = 0
        self.error_count = 0
        self.consecutive_errors = 0
        self.quality_score = 1.0  # 1.0 = perfect, 0.0 = broken
        self.periodic_interval = 5  # messages between periodic reminders
    
    def update_quality(self, parse_method: str):
        """Update quality score based on parse result.
        
        Uses exponential moving average to smooth oscillations.
        """
        # Target quality for this parse
        if parse_method == "clean":
            target = 1.0
            self.consecutive_errors = 0
        elif parse_method in ("salvaged", "extracted", "funccall"):
            target = 0.6
            self.consecutive_errors = 0
        elif parse_method == "failed":
            target = 0.0
            self.error_count += 1
            self.consecutive_errors += 1
        else:
            # Unknown method, assume salvaged
            target = 0.6
            self.consecutive_errors = 0
        
        # Exponential moving average with alpha=0.3 for smoothing
        # This prevents rapid oscillation
        alpha = 0.3
        self.quality_score = alpha * target + (1 - alpha) * self.quality_score
        
        # Clamp to valid range
        self.quality_score = max(0.0, min(1.0, self.quality_score))
        
        # Adapt frequency based on quality
        if self.quality_score > 0.8:
            self.periodic_interval = 10  # Rare
        elif self.quality_score > 0.5:
            self.periodic_interval = 5   # Normal
        else:
            self.periodic_interval = 2   # Frequent
        
        logger.debug(f"Quality updated: {self.quality_score:.2f}, interval={self.periodic_interval}")
    
    def should_remind(self, event: str = "normal") -> List[str]:
        """Determine which reminders are needed now.
        
        Events:
        - normal: regular turn
        - parse_error: failed to parse response
        - tool_results_received: after tool execution
        - chat_rotated: just created new chat
        """
        reminders = []
        
        # 1. PERIODIC - based on message count and quality
        if self.message_count > 0 and self.message_count % self.periodic_interval == 0:
            if self.quality_score < 0.7:
                reminders.append("format_strict")
            else:
                reminders.append("format_light")
        
        # 2. AFTER ERROR - immediately after parse failure
        if event == "parse_error":
            reminders.append("after_error")
            if self.consecutive_errors >= 2:
                reminders.append("anti_hallucination")
        
        # 3. AFTER TOOL RESULTS - remind about completion check
        if event == "tool_results_received":
            reminders.append("completion_check")
        
        # 4. AFTER ROTATION - reinforce format rules
        if event == "chat_rotated":
            reminders.extend(["format_strict", "anti_hallucination"])
        
        # 5. LOW QUALITY - extra reminders
        if self.quality_score < 0.3 and event == "normal":
            reminders.append("format_strict")
        
        return reminders
    
    def get_reminder_text(self, reminder_names: List[str]) -> str:
        """Combine multiple reminders into one text block."""
        if not reminder_names:
            return ""
        
        texts = [REMINDERS.get(name, "") for name in reminder_names]
        return "\n\n".join(t for t in texts if t)
    
    def needs_rotation(self) -> bool:
        """Check if chat quality is so bad it needs rotation."""
        return (
            self.quality_score < 0.2 or
            self.consecutive_errors > 5 or
            self.error_count > 10
        )
