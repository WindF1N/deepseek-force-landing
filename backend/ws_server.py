import asyncio
import json
import uuid
import logging
import time
from fastapi import WebSocket, WebSocketDisconnect
from typing import Optional, Dict, List
from config import settings

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """Rough token estimation: ~1.3 words per token."""
    if not text:
        return 0
    return int(len(text.split()) * 1.3)


class SessionManager:
    """Manages a single WebSocket connection (session) to a Chrome Extension instance."""

    def __init__(self, websocket: WebSocket, session_id: str):
        self.websocket = websocket
        self.session_id = session_id
        self.is_busy = False
        self._closed = False
        self.pending: Dict[str, asyncio.Future] = {}

    async def send_request(self, payload: dict) -> dict:
        """Send a request to the extension and wait for response.

        Assumes the caller has already marked this session busy (atomic acquire
        under the pool's condition). We do NOT flip is_busy here on entry; we
        only clear it in finally so the pool can wake the next waiter.
        """
        if self._closed:
            raise RuntimeError(f"Session {self.session_id} is closed")

        req_id = str(uuid.uuid4())
        request = {"type": "REQUEST", "requestId": req_id, "payload": payload}
        future = asyncio.get_event_loop().create_future()
        self.pending[req_id] = future

        try:
            await self.websocket.send_text(json.dumps(request))
            result = await asyncio.wait_for(future, timeout=300)
            return result
        except asyncio.TimeoutError:
            self.pending.pop(req_id, None)
            raise RuntimeError(f"Timeout waiting for extension {self.session_id}")

    def handle_response(self, msg: dict):
        """Handle a RESPONSE message from the extension."""
        req_id = msg.get("requestId")
        if req_id in self.pending:
            future = self.pending.pop(req_id)
            if not future.done():
                future.set_result(msg.get("result"))

    def close(self):
        """Mark the session as closed and clean up."""
        self._closed = True
        for future in self.pending.values():
            if not future.done():
                future.set_exception(RuntimeError("Session closed"))
        self.pending.clear()


class SessionPool:
    """Manages a pool of WebSocket sessions (multiple Chrome Extensions).

    Uses an asyncio.Condition as a fair waiting room: a request that finds all
    sessions busy parks on the condition until a session is released, instead
    of failing immediately. This removes the "No available session" 503s and
    the busy race condition when many parallel requests share few sessions.
    """

    def __init__(self):
        self.sessions: Dict[str, SessionManager] = {}
        self.connection_event = asyncio.Event()
        self._cond = asyncio.Condition()

    async def connect(
        self, websocket: WebSocket, key: str = None, session_id: str = None
    ) -> Optional[SessionManager]:
        """Accept a new WebSocket connection and create a session."""
        if key != settings.WS_AUTH_KEY:
            await websocket.close(code=4001)
            return None

        if session_id is None:
            session_id = str(uuid.uuid4())

        # If session_id already exists, close the old one
        if session_id in self.sessions:
            old_session = self.sessions[session_id]
            old_session.close()
            del self.sessions[session_id]

        await websocket.accept()
        session = SessionManager(websocket, session_id)
        self.sessions[session_id] = session
        self.connection_event.set()
        # wake any requests parked waiting for a session
        async with self._cond:
            self._cond.notify_all()
        logger.info(
            f"Extension connected: session_id={session_id}, total={len(self.sessions)}"
        )

        # Message loop directly inside this task (stable, like old ExtensionManager)
        try:
            while True:
                data = await websocket.receive_text()
                msg = json.loads(data)
                msg_type = msg.get("type")

                if msg_type == "PING":
                    await websocket.send_text(json.dumps({"type": "PONG"}))
                elif msg_type == "RESPONSE":
                    session.handle_response(msg)
                else:
                    logger.warning(
                        f"Unknown message type {msg_type} from {session_id}"
                    )
        except WebSocketDisconnect:
            logger.warning(f"Extension disconnected: session_id={session_id}")
        except Exception as e:
            logger.exception(f"Error handling messages for {session_id}: {e}")
        finally:
            session.close()
            if session_id in self.sessions:
                del self.sessions[session_id]
            if not self.sessions:
                self.connection_event.clear()
            # wake parked requests so they re-check (pick another session or fail)
            async with self._cond:
                self._cond.notify_all()
            logger.info(
                f"Removed session {session_id}, remaining={len(self.sessions)}"
            )

    def has_available_connection(self) -> bool:
        """Check if there is at least one available session."""
        for session in self.sessions.values():
            if not session.is_busy and not session._closed:
                return True
        return False

    def get_connection_count(self) -> int:
        """Get total number of active sessions."""
        return len(self.sessions)

    async def wait_for_connection(self, timeout: float = 10.0) -> bool:
        """Wait for at least one session to become available."""
        if self.sessions:
            return True
        try:
            await asyncio.wait_for(self.connection_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def send_request(self, payload: dict, queue_timeout: float = 120.0) -> dict:
        """Acquire a free session (waiting in a fair queue if all are busy),
        run the request, then release the session and wake the next waiter.

        Raises RuntimeError only if no session frees up within queue_timeout or
        there are genuinely no sessions connected at all.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + queue_timeout

        async with self._cond:
            while True:
                # pick a free, open session and claim it atomically
                for session in self.sessions.values():
                    if not session.is_busy and not session._closed:
                        session.is_busy = True
                        chosen = session
                        break
                else:
                    chosen = None

                if chosen is not None:
                    break

                if not self.sessions:
                    raise RuntimeError("No extension connected")

                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise RuntimeError("Timed out waiting for a free session")
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    raise RuntimeError("Timed out waiting for a free session")

        # run outside the lock so other waiters can be scheduled
        try:
            return await chosen.send_request(payload)
        finally:
            chosen.is_busy = False
            async with self._cond:
                self._cond.notify(1)


# Global instance
extension_manager = SessionPool()


# ============================================================================
# PERSISTENT CHAT CONTEXT (новая архитектура)
# ============================================================================

class DeepSeekChat:
    """One DeepSeek chat session (persistent context)."""
    
    def __init__(self, chat_session_id: str, pool: 'SessionPool'):
        self.chat_session_id = chat_session_id
        self.pool = pool
        self.parent_message_id: Optional[int] = None  # ← INT, not string!
        self.token_count = 0
        self.created_at = time.time()
        self.last_request_time = 0  # Track last request timestamp for rate limiting
    
    async def send(self, prompt: str, model_type: str = None, 
                   thinking_enabled: bool = True, 
                   search_enabled: bool = False,
                   images: list = None) -> dict:
        """Send a message to this persistent chat.
        
        Args:
            prompt: The message text
            model_type: "vision" or None (default chat)
            thinking_enabled: Enable deepthink reasoning (default True)
            search_enabled: Enable web search
            images: List of image URLs/base64 for vision
        """
        # Rate limiting: enforce minimum delay between requests to avoid "Messages too frequent"
        MIN_DELAY_SECONDS = 3.0  # DeepSeek rate limit protection (enforced AFTER response received)
        
        if self.last_request_time > 0:
            elapsed = time.time() - self.last_request_time
            if elapsed < MIN_DELAY_SECONDS:
                wait_time = MIN_DELAY_SECONDS - elapsed
                logger.info(f"[RATE LIMIT] Waiting {wait_time:.2f}s since last RESPONSE (min delay: {MIN_DELAY_SECONDS}s)")
                await asyncio.sleep(wait_time)
        
        # First message? Create chat session with this message
        if self.chat_session_id is None:
            # Create new chat by sending first message with parent_message_id=null
            # DeepSeek creates the chat automatically
            logger.info("[DEBUG] Creating new chat session with first message")
        else:
            logger.info(f"[DEBUG] Continuing existing chat: chat_session_id={self.chat_session_id}, parent_message_id={self.parent_message_id}")
        
        payload = {
            "action": "continue_chat",
            "chat_session_id": self.chat_session_id,  # None on first call
            "parent_message_id": self.parent_message_id,  # None on first call
            "prompt": prompt,
            "thinking_enabled": thinking_enabled,
            "search_enabled": search_enabled,
            "images": images or []
        }
        
        # Only add model_type if specified (avoid sending null)
        if model_type is not None:
            payload["model_type"] = model_type
        
        # DEBUG: Log full payload
        logger.info(f"[DEBUG] Sending payload to extension: {payload}")
        
        # Infinite retry with exponential backoff for 502 errors (DeepSeek overload)
        retry_count = 0
        max_delay = 30  # Cap at 30 seconds
        
        while True:
            try:
                # Get session from pool (avoid closed sessions)
                result = await self.pool.send_request(payload)
                
                # DEBUG: Log full result
                logger.info(f"[DEBUG] Got result from extension: {result}")
                
                if result.get("success"):
                    # Update chat_session_id if this was first message
                    if self.chat_session_id is None and result.get("chat_session_id"):
                        self.chat_session_id = result["chat_session_id"]
                        logger.info(f"Chat session created: {self.chat_session_id}")
                    
                    # Update parent_message_id ONLY if response has actual content
                    # (skip empty responses from rate limits / errors - prevents broken chat chain)
                    new_message_id = result.get("message_id")
                    content = result.get("content", "")
                    
                    if new_message_id is not None and content and content.strip():
                        self.parent_message_id = new_message_id
                        logger.info(f"[DEBUG] Updated parent_message_id to {new_message_id}")
                    else:
                        if new_message_id is not None and not content.strip():
                            logger.warning(f"[DEBUG] Skipping parent_message_id update - empty content (rate limit/error), message_id={new_message_id}")
                        else:
                            logger.info(f"[DEBUG] No message_id in response, keeping parent_message_id={self.parent_message_id}")
                    
                    self.token_count += estimate_tokens(prompt)
                    self.token_count += estimate_tokens(result.get("content", ""))
                    
                    # Update rate limit timestamp AFTER receiving response (not before sending!)
                    # This ensures MIN_DELAY between response→request, not request→request
                    self.last_request_time = time.time()
                    
                    if retry_count > 0:
                        logger.info(f"Request succeeded after {retry_count} retries")
                    
                    return result
                else:
                    # Check if it's a retryable error (DeepSeek overload)
                    error = result.get("error", "")
                    error_str = str(error).lower()
                    
                    # Retry on: 502 (overload), 503 (service unavailable), timeouts, expert busy, rate limit
                    # 422 is validation error - should NOT retry (fix the request instead!)
                    if any(code in str(error) for code in ["502", "503", "timeout"]) or \
                       any(keyword in error_str for keyword in ["bad gateway", "overload", "service unavailable", "timeout", 
                                                                  "server is busy", "expert busy", "rate limit"]):
                        retry_count += 1
                        delay = min(2 ** min(retry_count, 5), max_delay)  # Exponential backoff: 2, 4, 8, 16, 32, 30...
                        logger.warning(f"DeepSeek retryable error (attempt {retry_count}), waiting {delay}s: {error}")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        # Other error (422, 400, etc.) - return immediately (don't retry validation errors!)
                        logger.error(f"Non-retryable error: {error}")
                        return result
                        
            except Exception as e:
                # Network/connection errors - also retry
                retry_count += 1
                delay = min(2 ** min(retry_count, 5), max_delay)
                logger.warning(f"Connection error, retry {retry_count} after {delay}s: {e}")
                await asyncio.sleep(delay)
                continue
    
    async def close(self):
        """Close this chat (just forget the ID, no API call needed)."""
        pass


class UserChatContext:
    """Persistent chat context for one Hermes user/conversation."""
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.primary_chat: Optional[DeepSeekChat] = None
        self.lock = asyncio.Lock()
        
        self.context_summary = ""
        self.message_count = 0
        self.max_tokens = 100_000
        
        # Reminder scheduler (will be initialized by orchestrator)
        self.reminder_scheduler = None
        
        self.last_used = time.time()


class ChatContextManager:
    """Maps Hermes conversations to persistent DeepSeek chats."""
    
    def __init__(self, pool: SessionPool):
        self.pool = pool
        self.contexts: Dict[str, UserChatContext] = {}
        self._cleanup_task = None
    
    def start_cleanup(self):
        """Start background TTL cleanup task."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def get_or_create(self, user_id: str) -> UserChatContext:
        """Get existing context or create new one."""
        logger.info(f"[DEBUG] get_or_create called for user_id={user_id}")
        logger.info(f"[DEBUG] Current contexts in memory: {list(self.contexts.keys())}")
        
        if user_id in self.contexts:
            ctx = self.contexts[user_id]
            ctx.last_used = time.time()
            logger.info(f"[DEBUG] FOUND existing context for {user_id}")
            logger.info(f"[DEBUG] Existing chat_session_id={ctx.primary_chat.chat_session_id}, parent_message_id={ctx.primary_chat.parent_message_id}")
            return ctx
        
        # Create new context WITHOUT creating DeepSeek chat yet
        # The chat will be created on first send() call
        logger.info(f"[DEBUG] NOT FOUND - creating NEW context for {user_id}")
        ctx = UserChatContext(user_id)
        
        # Create DeepSeekChat placeholder (chat_session_id=None, will be created on first message)
        ctx.primary_chat = DeepSeekChat(None, self.pool)
        
        self.contexts[user_id] = ctx
        logger.info(f"[DEBUG] Created context for user {user_id} (chat will be created on first message)")
        logger.info(f"[DEBUG] Contexts after creation: {list(self.contexts.keys())}")
        
        return ctx
    
    async def _cleanup_loop(self):
        """TTL cleanup: DISABLED - contexts persist indefinitely."""
        # No automatic cleanup - contexts remain until backend restart
        # This ensures persistent chat context is never lost due to idle time
        while True:
            await asyncio.sleep(3600)  # Sleep forever (check every hour but do nothing)
            # No cleanup logic - contexts persist indefinitely


# Global chat context manager
chat_context_manager = ChatContextManager(extension_manager)