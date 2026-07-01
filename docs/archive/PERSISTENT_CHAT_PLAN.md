# Полный план: Persistent Chat + Adaptive Reminders

## Цель

Превратить архитектуру из "каждый запрос = новый чат" в "один диалог Hermes = один DeepSeek чат с накопленным контекстом + адаптивные напоминания о формате".

---

## Этап 1: ChatContextManager (базовая структура)

### 1.1 Новые классы в ws_server.py

```python
class DeepSeekChat:
    """Один DeepSeek чат (chat_session_id)."""
    def __init__(self, chat_session_id: str, session: SessionManager):
        self.chat_session_id = chat_session_id
        self.parent_message_id = None  # для цепочки сообщений
        self.session = session  # привязка к WebSocket
        self.token_count = 0
        self.created_at = time.time()
    
    async def send(self, prompt: str, model: str = None, images: list = None) -> dict:
        """Отправить сообщение в этот чат."""
        result = await self.session.send_request({
            "action": "continue_chat",
            "chat_session_id": self.chat_session_id,
            "parent_message_id": self.parent_message_id,
            "prompt": prompt,
            "model_type": model,
            "images": images
        })
        
        if result.get("success"):
            self.parent_message_id = result.get("message_id")
            self.token_count += estimate_tokens(prompt) + estimate_tokens(result.get("content", ""))
        
        return result
    
    async def close(self):
        """Закрыть чат (опционально — можем просто забыть session_id)."""
        pass


class UserChatContext:
    """Контекст одного пользователя Hermes."""
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.primary_chat: Optional[DeepSeekChat] = None
        self.lock = asyncio.Lock()  # serialization per user
        
        self.context_summary = ""  # сжатая история после ротации
        self.message_count = 0
        self.max_tokens = 100_000  # порог ротации
        
        self.last_used = time.time()


class ChatContextManager:
    """Управляет маппингом Hermes → DeepSeek chats."""
    def __init__(self, pool: SessionPool):
        self.pool = pool
        self.contexts: Dict[str, UserChatContext] = {}
        asyncio.create_task(self._cleanup_loop())
    
    async def get_or_create(self, user_id: str) -> UserChatContext:
        """Получить или создать контекст."""
        if user_id in self.contexts:
            ctx = self.contexts[user_id]
            ctx.last_used = time.time()
            return ctx
        
        # Создаём новый контекст
        ctx = UserChatContext(user_id)
        
        # Создаём DeepSeek чат
        session = await self.pool.acquire()
        try:
            result = await session.send_request({"action": "create_chat_session"})
            chat_session_id = result["chat_session_id"]
            ctx.primary_chat = DeepSeekChat(chat_session_id, session)
        finally:
            self.pool.release(session)
        
        self.contexts[user_id] = ctx
        return ctx
    
    async def _cleanup_loop(self):
        """TTL: очистка неактивных чатов каждые 60 секунд."""
        while True:
            await asyncio.sleep(60)
            now = time.time()
            to_remove = []
            
            for user_id, ctx in self.contexts.items():
                if now - ctx.last_used > 300:  # 5 минут idle
                    logger.info(f"Releasing idle chat for user {user_id}")
                    await ctx.primary_chat.close()
                    to_remove.append(user_id)
            
            for user_id in to_remove:
                del self.contexts[user_id]
```

### 1.2 Изменения в content.js

Добавить поддержку двух action:

```javascript
async function handleApiRequest(payload) {
  const { action } = payload;
  
  if (action === 'create_chat_session') {
    // Создать новый чат
    const chatId = await createChatSession();
    return { success: true, chat_session_id: chatId };
  }
  
  if (action === 'continue_chat') {
    // Продолжить существующий чат
    const { chat_session_id, parent_message_id, prompt, model_type, images } = payload;
    
    const leim = await getHifLeim();
    const dliq = await getHifDliq();
    
    // Загрузка файлов (если есть)
    let refFileIds = [];
    if (images?.length) {
      refFileIds = await uploadImages(images, leim, dliq);
    }
    
    // PoW для completion
    const powChallenge = await createPowChallenge('/api/v0/chat/completion');
    const powToken = await getPowToken(powChallenge.data.biz_data);
    
    // Отправка в существующий чат
    const resp = await fetch('https://chat.deepseek.com/api/v0/chat/completion', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${authToken}`,
        'x-ds-pow-response': powToken,
        'x-hif-leim': leim,
        'x-hif-dliq': dliq,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        chat_session_id: chat_session_id,
        parent_message_id: parent_message_id || null,
        model_type: model_type || null,
        prompt: prompt,
        ref_file_ids: refFileIds,
        thinking_enabled: false,
        search_enabled: false
      })
    });
    
    const content = await parseSSE(resp.body);
    
    // Сохраняем message_id для следующего хода
    const messageId = extractMessageId(resp);  // нужно добавить extraction из headers/body
    
    return {
      success: true,
      content: content,
      message_id: messageId
    };
  }
}
```

---

## Этап 2: Prompt Splitting (system init vs current turn)

### 2.1 Новые функции в prompt.py

```python
def build_system_init(messages: list, tools: list) -> str:
    """Инициализация чата — отправляется ОДИН РАЗ."""
    system_msg = next((m for m in messages if m['role'] == 'system'), None)
    
    init = "You are an AI assistant with tool-calling abilities.\n\n"
    
    if system_msg:
        init += system_msg.get('content', '') + "\n\n"
    
    init += "AVAILABLE TOOLS:\n"
    init += json.dumps(tools, indent=2, ensure_ascii=False) + "\n\n"
    
    init += """RULES:
1. When you need to perform an action, emit a tool call in JSON format.
2. You can provide reasoning BEFORE the JSON (explain your thinking).
3. NEVER claim work is done without calling the required tool.
4. You have NO file system access — only via tools.

FORMAT:
Option A (with reasoning):
  Your explanation here.
  
  {"tool_calls": [{"name": "tool_name", "arguments": {...}}]}

Option B (direct):
  {"tool_calls": [{"name": "tool_name", "arguments": {...}}]}

Option C (final answer, no tool):
  Plain text response when task is complete.

Respond "Ready" to confirm you understand."""
    
    return init


def build_current_turn(messages: list) -> str:
    """Только текущий ход — последний user message + tool results."""
    # Берём последние N сообщений до предыдущего user message
    recent = []
    user_count = 0
    
    for m in reversed(messages):
        recent.insert(0, m)
        if m['role'] == 'user':
            user_count += 1
            if user_count == 2:  # дошли до предыдущего user — хватит
                break
    
    # Форматируем
    parts = []
    for m in recent:
        role = m['role']
        content = m.get('content', '')
        
        if role == 'user':
            parts.append(f"USER REQUEST:\n{content}")
        elif role == 'tool':
            tool_name = m.get('name', 'unknown')
            parts.append(f"TOOL RESULT ({tool_name}):\n{content}")
        elif role == 'assistant' and m.get('tool_calls'):
            # Пропускаем assistant tool_calls — они уже в истории DeepSeek
            pass
    
    return "\n\n".join(parts)
```

### 2.2 Обновить api.py для использования init + turn

```python
async def _run_tool_loop(req: ChatRequest) -> dict:
    messages = [m.model_dump() for m in req.messages]
    
    # Определяем user_id (из заголовка или хэш первых сообщений)
    user_id = req.headers.get('X-Conversation-ID') or hashlib.md5(
        json.dumps(messages[:2]).encode()
    ).hexdigest()
    
    ctx = await chat_context_manager.get_or_create(user_id)
    
    async with ctx.lock:  # serialization per user
        # Первый ход — инициализация
        is_first_turn = ctx.message_count == 0
        
        if is_first_turn:
            # Отправляем ВЕСЬ system prompt + tools
            init_prompt = prompt_builder.build_system_init(messages, req.tools)
            
            result = await ctx.primary_chat.send(init_prompt, model=ds_model)
            logger.info(f"Initialized chat {ctx.primary_chat.chat_session_id} for user {user_id}")
            
            # DeepSeek должен ответить "Ready" или что-то подобное
            # Можем игнорировать этот ответ или проверить
        
        # Проверка overflow
        if ctx.primary_chat.token_count > ctx.max_tokens:
            await rotate_chat(ctx, req.tools)
        
        # Формируем текущий ход
        current_prompt = prompt_builder.build_current_turn(messages)
        
        # Извлекаем images
        images = prompt_builder.extract_images(messages)
        if images:
            ds_model = "deepseek-vision"
        
        # Отправляем
        result = await ctx.primary_chat.send(current_prompt, model=ds_model, images=images)
        ctx.message_count += 1
        
        # Парсим tool_calls как раньше
        tool_calls, content, how = toolcall.parse(result['content'])
        
        if tool_calls:
            ok, err = validator.validate_tool_calls(tool_calls, req.tools)
            if ok:
                return _resp_envelope(req.model, content=content, tool_calls=tool_calls[:1])
            
            # Recovery...
        
        if content:
            return _resp_envelope(req.model, content=content)
        
        # Fallback
        return _resp_envelope(req.model, content="[Empty response]")
```

---

## Этап 3: Ротация чатов (context overflow)

```python
async def rotate_chat(ctx: UserChatContext, tools: list):
    """Переполнение контекста → новый чат с суммаризацией."""
    logger.info(f"Rotating chat for user {ctx.user_id} (tokens: {ctx.primary_chat.token_count})")
    
    # 1. Суммаризировать старый чат
    summary_prompt = """Summarize this conversation in 500 words max.
Focus on:
- User's original goal
- Completed actions (files created, commands run, tools used)
- Current state and results
- Pending tasks

Be concise but preserve critical details."""
    
    summary_result = await ctx.primary_chat.send(summary_prompt)
    ctx.context_summary = summary_result.get('content', '')
    
    # 2. Закрыть старый чат
    old_chat_id = ctx.primary_chat.chat_session_id
    await ctx.primary_chat.close()
    
    # 3. Создать новый чат
    session = await chat_context_manager.pool.acquire()
    try:
        result = await session.send_request({"action": "create_chat_session"})
        new_chat_id = result["chat_session_id"]
        ctx.primary_chat = DeepSeekChat(new_chat_id, session)
    finally:
        chat_context_manager.pool.release(session)
    
    # 4. Инициализировать с контекстом
    init_with_context = f"""You are an AI assistant with tool-calling abilities.

AVAILABLE TOOLS:
{json.dumps(tools, indent=2)}

RULES:
[same as before]

PRIOR CONTEXT (from previous session):
{ctx.context_summary}

The user will continue from where they left off. Respond "Ready" to confirm."""
    
    await ctx.primary_chat.send(init_with_context)
    
    logger.info(f"Rotated chat: {old_chat_id} → {new_chat_id}")
    ctx.message_count = 0  # reset counter
```

---

## Этап 4: Адаптивные напоминания

### 4.1 ReminderScheduler

```python
class ReminderScheduler:
    def __init__(self):
        self.message_count = 0
        self.error_count = 0
        self.quality_score = 1.0
        self.periodic_interval = 5
    
    def update_quality(self, parse_method: str):
        if parse_method == "clean":
            self.quality_score = min(1.0, self.quality_score + 0.1)
        elif parse_method == "salvaged":
            self.quality_score = max(0.3, self.quality_score - 0.1)
        elif parse_method == "failed":
            self.quality_score = max(0.0, self.quality_score - 0.3)
            self.error_count += 1
        else:
            self.error_count = 0
        
        # Адаптируем частоту
        if self.quality_score > 0.8:
            self.periodic_interval = 10
        elif self.quality_score > 0.5:
            self.periodic_interval = 5
        else:
            self.periodic_interval = 2
    
    def should_remind(self, event: str) -> List[str]:
        reminders = []
        
        # Periodic
        if self.message_count % self.periodic_interval == 0 and self.message_count > 0:
            reminders.append("format")
        
        # After error
        if event == "parse_error":
            reminders.append("format_strict")
            if self.error_count >= 2:
                reminders.append("anti_hallucination")
        
        # After tool results
        if event == "tool_results_received":
            reminders.append("completion_check")
        
        # After rotation
        if event == "chat_rotated":
            reminders.extend(["format", "anti_hallucination"])
        
        return reminders
```

### 4.2 Библиотека напоминаний

```python
REMINDERS = {
    "format": """
[Respond with tool call JSON or final text answer]""",
    
    "format_strict": """
⚠️ FORMAT REMINDER:
Tool call: {"tool_calls": [{"name": "exact_name", "arguments": {...}}]}
Final answer: Plain text (no JSON)
Choose ONE. No markdown blocks, no mixing.""",
    
    "anti_hallucination": """
⚠️ CRITICAL: You cannot act directly. Only via tool calls.
Do not claim "file created" or "done" unless tool result confirms it.""",
    
    "completion_check": """
Before saying "done", verify ALL parts of user's request are complete.
If anything is missing → emit the required tool call."""
}
```

### 4.3 Интеграция в UserChatContext

```python
class UserChatContext:
    def __init__(self, user_id: str):
        # ... existing fields ...
        self.reminder_scheduler = ReminderScheduler()
    
    async def send_with_reminders(self, prompt: str, model: str, images: list, event: str = None):
        # Определяем нужные напоминания
        reminders_needed = self.reminder_scheduler.should_remind(event or "normal")
        
        # Добавляем напоминания в промпт
        if reminders_needed:
            reminder_text = "\n\n".join([REMINDERS[r] for r in reminders_needed])
            prompt = f"{prompt}\n\n{reminder_text}"
        
        # Отправка
        result = await self.primary_chat.send(prompt, model, images)
        
        # Обновляем метрики
        self.message_count += 1
        
        return result
```

---

## Этап 5: Тестирование

### 5.1 Юнит-тесты

- `test_chat_context_init()` — создание чата и инициализация
- `test_chat_rotation()` — overflow и перенос контекста
- `test_ttl_cleanup()` — очистка неактивных чатов
- `test_reminders_periodic()` — периодические напоминания
- `test_reminders_adaptive()` — адаптация частоты

### 5.2 Интеграционный тест

Полный диалог Hermes → DeepSeek:
1. Init chat (system + tools)
2. User request
3. Tool calls
4. Tool results
5. Multiple turns
6. Rotation при overflow
7. Проверка reasoning в ответах

---

## Порядок реализации

1. ✅ **Reasoning extraction** (ГОТОВО)
2. **ChatContextManager + DeepSeekChat** — базовые структуры
3. **content.js** — поддержка continue_chat
4. **prompt.py** — splitting на init/turn
5. **api.py** — интеграция persistent chat
6. **rotate_chat()** — механизм переноса контекста
7. **ReminderScheduler** — адаптивные напоминания
8. **TTL cleanup** — автоочистка
9. **Тесты** — юнит + интеграционные

---

## Метрики успеха

- ✅ Размер промпта снижен в 5-10 раз (system+tools слаются один раз)
- ✅ DeepSeek видит полную историю диалога
- ✅ Ротация работает без потери контекста
- ✅ Напоминания снижают количество ошибок парсинга
- ✅ Reasoning показывается пользователю в Hermes
- ✅ TTL корректно освобождает ресурсы

---

Начинаем с этапа 2 (ChatContextManager)?
