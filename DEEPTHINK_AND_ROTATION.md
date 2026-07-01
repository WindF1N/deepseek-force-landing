# DEEPTHINK & CHAT ROTATION STRATEGY

## ВОПРОС 1: Когда использовать thinking_enabled?

### Что такое thinking_enabled (DeepThink)?

**DeepThink** = Chain-of-Thought reasoning в DeepSeek:
- Модель показывает внутренние рассуждения
- Более качественные ответы на сложные задачи
- Медленнее (2-3x время ответа)
- Больше токенов

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## СТРАТЕГИЯ: Адаптивный DeepThink

### Вариант A: Эвристика по типу задачи

```python
def should_use_deepthink(prompt: str, context: dict) -> bool:
    """
    Решаем использовать ли deepthink на основе эвристик.
    """
    
    # 1. Ключевые слова сложных задач
    complex_keywords = [
        "почему", "объясни", "как работает", "разница между",
        "анализ", "compare", "debug", "fix", "optimize",
        "architecture", "design", "plan", "strategy"
    ]
    
    if any(kw in prompt.lower() for kw in complex_keywords):
        return True
    
    # 2. Длинный промпт = сложная задача
    if len(prompt) > 1000:
        return True
    
    # 3. Наличие кода в промпте
    if "```" in prompt or "def " in prompt or "function" in prompt:
        return True
    
    # 4. Tool failures в контексте (нужно думать!)
    if context.get("consecutive_failures", 0) > 1:
        return True
    
    # 5. Простые задачи - без deepthink
    simple_patterns = [
        "привет", "создай файл", "удали", "прочитай",
        "hello", "hi", "thanks", "спасибо"
    ]
    
    if any(p in prompt.lower() for p in simple_patterns) and len(prompt) < 100:
        return False
    
    # Default: включаем deepthink (лучше качество)
    return True
```

### Примеры:

```python
# ✅ DeepThink ON (сложные задачи)
"Почему этот код не работает?"                    → thinking=True
"Разработай архитектуру для microservices"        → thinking=True
"Debug: RuntimeError in line 42"                  → thinking=True
"Оптимизируй этот SQL запрос"                     → thinking=True

# ❌ DeepThink OFF (простые задачи)
"Привет!"                                         → thinking=False
"Создай файл test.txt"                           → thinking=False
"Спасибо!"                                       → thinking=False
"Удали /tmp/old.txt"                             → thinking=False
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Вариант B: Hermes указывает явно

Если Hermes добавит поддержку reasoning mode:

```python
# В Hermes request
{
  "model": "deepseek-expert",
  "messages": [...],
  "reasoning_effort": "high"  # low/medium/high
}

# Маппинг:
reasoning_effort = "high"   → thinking_enabled = True
reasoning_effort = "low"    → thinking_enabled = False
reasoning_effort = None     → auto (эвристика)
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Вариант C: Всегда включён (самый простой)

```python
# Всегда используем deepthink
thinking_enabled = True

# Плюсы:
# ✅ Максимальное качество
# ✅ Простая логика

# Минусы:
# ❌ Медленнее на простых задачах
# ❌ Больше токенов
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### РЕКОМЕНДАЦИЯ: Вариант A (эвристика)

```python
# orchestrator/api.py

async def _run_persistent_chat(req: ChatRequest):
    user_id = generate_user_id(req)
    messages = req.messages
    last_msg = messages[-1]
    
    # Определяем нужен ли deepthink
    context = {
        "consecutive_failures": get_failure_count(user_id),
        "task_type": detect_task_type(messages)
    }
    
    use_deepthink = should_use_deepthink(
        prompt=last_msg.content,
        context=context
    )
    
    # Отправляем в primary chat
    primary_chat = await backend.get_primary_chat(user_id)
    response = await primary_chat.send(
        prompt=format_prompt(last_msg),
        thinking_enabled=use_deepthink  # ← адаптивно!
    )
    
    return response
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## ВОПРОС 2: Что делать когда чат переполнен?

### Проблема: Chat Limits

DeepSeek имеет лимиты:
- **Макс сообщений в чате**: ~100-200 messages
- **Макс токенов контекста**: ~100K tokens
- **Ошибка**: "context length exceeded" или "chat too long"

**Пример:**
```
Turn 1-50:   Всё ОК ✅
Turn 51-100: Всё ОК ✅
Turn 101:    ❌ "Context length exceeded"
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### СТРАТЕГИЯ: Chat Rotation с Summarization

```
┌────────────────────────────────────────────────────────────┐
│ CHAT LIFECYCLE                                              │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  [CHAT 1] chat_session_id = "abc123_gen1"                 │
│  Turn 1-100: Нормальная работа                            │
│  Status: Full (100 messages)                              │
│                                                            │
│  ↓ Достигнут лимит, создаём новый чат                     │
│                                                            │
│  [SUMMARIZATION]                                           │
│  → Берём последние 20 messages из Chat 1                  │
│  → Создаём summary через DeepSeek                         │
│  → Summary: "User working on apple website project..."    │
│                                                            │
│  ↓                                                         │
│                                                            │
│  [CHAT 2] chat_session_id = "abc123_gen2"                 │
│  msg_001: [System instructions] + [Summary] + New message │
│  Turn 101-200: Продолжаем с контекстом                    │
│                                                            │
│  ↓ Достигнут лимит снова                                  │
│                                                            │
│  [CHAT 3] chat_session_id = "abc123_gen3"                 │
│  msg_001: [System] + [Summary of Chat 1+2] + New message  │
│  ...                                                       │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Реализация: ChatRotation Manager

```python
class DeepSeekChat:
    def __init__(self, chat_session_id, session, mode="default"):
        self.chat_session_id = chat_session_id
        self.parent_message_id = None
        self.session = session
        self.mode = mode
        
        # Tracking для rotation
        self.message_count = 0
        self.generation = 1  # какое поколение чата
        self.summary = None  # summary предыдущих чатов
    
    async def send(self, prompt, thinking_enabled=True, **kwargs):
        # Проверка: не переполнен ли чат?
        if self.is_near_limit():
            raise ChatLimitError("Chat approaching limit, need rotation")
        
        # Обычная отправка
        result = await self._send_message(prompt, thinking_enabled, **kwargs)
        
        self.message_count += 1
        self.parent_message_id = result["response_message_id"]
        
        return result
    
    def is_near_limit(self) -> bool:
        """
        Проверяем приближается ли лимит.
        
        Лимиты (консервативные):
        - 80 messages (оставляем запас)
        - Или error response от DeepSeek
        """
        return self.message_count >= 80


class ChatContextManager:
    async def rotate_primary_chat(self, user_id: str):
        """
        Создать новый primary chat с summary старого.
        """
        ctx = self.contexts[user_id]
        old_chat = ctx.primary_chat
        
        # 1. Создаём summary старого чата
        logger.info(f"Rotating chat for {user_id}, gen {old_chat.generation}")
        
        summary = await self._create_summary(old_chat, user_id)
        
        # 2. Создаём новый chat_session_id
        result = await self.pool.send_request({
            "action": "create_chat_session"
        })
        new_session_id = result["chat_session_id"]
        
        # 3. Создаём новый DeepSeekChat
        session = await self.pool.get_session()
        new_chat = DeepSeekChat(new_session_id, session, mode="default")
        new_chat.generation = old_chat.generation + 1
        new_chat.summary = summary
        
        # 4. Заменяем в контексте
        ctx.primary_chat = new_chat
        
        logger.info(f"Rotated to gen {new_chat.generation}, summary len={len(summary)}")
        
        return new_chat
    
    async def _create_summary(self, old_chat: DeepSeekChat, user_id: str):
        """
        Создать summary через helper чат.
        """
        # Используем отдельный summary чат (или vision/search)
        # чтобы не загрязнять primary
        
        # Получаем последние N messages из старого чата
        # (нужно хранить историю локально или через DeepSeek API)
        recent_messages = self._get_recent_messages(old_chat, limit=20)
        
        # Формируем промпт для summarization
        summary_prompt = f"""
Summarize this conversation history in 3-5 sentences.
Focus on: current task, files created, tools used, important decisions.

Conversation:
{recent_messages}

Summary:"""
        
        # Отправляем в отдельный чат для summarization
        result = await self.pool.send_request({
            "action": "chat_completion",
            "prompt": summary_prompt,
            "model_type": None,
            "thinking_enabled": False
        })
        
        summary = result["content"]
        
        return summary
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Orchestrator Integration

```python
# orchestrator/api.py

async def _run_persistent_chat(req: ChatRequest):
    user_id = generate_user_id(req)
    
    try:
        primary_chat = await backend.get_primary_chat(user_id)
        
        # Формируем промпт
        if primary_chat.parent_message_id is None:
            # Первое сообщение - добавляем system + summary
            prompt = format_initial_prompt(req, primary_chat.summary)
        else:
            # Продолжение
            prompt = format_continuation(req)
        
        # Отправляем
        response = await primary_chat.send(
            prompt=prompt,
            thinking_enabled=should_use_deepthink(prompt)
        )
        
        return format_response(response)
    
    except ChatLimitError:
        # Чат переполнен - делаем rotation
        logger.info(f"Chat limit reached for {user_id}, rotating...")
        
        new_chat = await backend.rotate_primary_chat(user_id)
        
        # Пробуем снова с новым чатом
        prompt = format_initial_prompt(req, new_chat.summary)
        response = await new_chat.send(
            prompt=prompt,
            thinking_enabled=should_use_deepthink(prompt)
        )
        
        return format_response(response)
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Пример: Chat Rotation в действии

```
═══════════════════════════════════════════════════════════════
CHAT 1 (gen=1, chat_id="abc_gen1")
═══════════════════════════════════════════════════════════════

Turn 1: User: "Создай сайт про яблоки"
Turn 2: Agent: write_file(index.html)
Turn 3: Tool: "Created"
...
Turn 78: User: "Добавь форму заказа"
Turn 79: Agent: patch(index.html, add form)
Turn 80: Tool: "Patched"

[message_count = 80, approaching limit!]

═══════════════════════════════════════════════════════════════
ROTATION: Creating summary...
═══════════════════════════════════════════════════════════════

Summary prompt: "Summarize last 20 messages..."
Summary result: "User is building an apple shop website.
                 Created index.html with hero section, product
                 cards, and order form. Using green theme,
                 responsive design. Files: apple-shop/index.html,
                 styles.css, script.js"

═══════════════════════════════════════════════════════════════
CHAT 2 (gen=2, chat_id="abc_gen2")
═══════════════════════════════════════════════════════════════

Turn 81 → msg_001 in new chat:
  [System instructions 40K]
  
  [Previous conversation summary]:
  User is building an apple shop website...
  
  User: "Теперь добавь payment integration"

Turn 82 → msg_002:
  Agent: "I'll add Stripe integration..."

Turn 83-160: Continue normally...

[message_count = 80 again]

═══════════════════════════════════════════════════════════════
ROTATION #2: Creating summary...
═══════════════════════════════════════════════════════════════

Summary: "User built apple shop website (from previous chat).
          Now added payment integration with Stripe, checkout
          page, and order confirmation. Testing in progress."

═══════════════════════════════════════════════════════════════
CHAT 3 (gen=3, chat_id="abc_gen3")
═══════════════════════════════════════════════════════════════

Turn 161 → msg_001:
  [System 40K]
  [Summary of Chat 1+2]
  User: "Deploy to Vercel"

...
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Хранение Message History (для summarization)

```python
class DeepSeekChat:
    def __init__(self, chat_session_id, session, mode="default"):
        self.chat_session_id = chat_session_id
        # ...
        
        # Храним последние N messages локально
        self.message_history = deque(maxlen=30)  # последние 30
    
    async def send(self, prompt, **kwargs):
        result = await self._send_message(prompt, **kwargs)
        
        # Сохраняем в историю
        self.message_history.append({
            "prompt": prompt,
            "response": result["content"],
            "timestamp": time.time(),
            "message_id": result["response_message_id"]
        })
        
        self.message_count += 1
        return result


class ChatContextManager:
    async def _create_summary(self, old_chat: DeepSeekChat, user_id: str):
        # Берём последние 20 messages из локальной истории
        recent = list(old_chat.message_history)[-20:]
        
        # Форматируем для summary
        formatted = []
        for msg in recent:
            formatted.append(f"User: {msg['prompt']}")
            formatted.append(f"Agent: {msg['response']}")
        
        conversation_text = "\n".join(formatted)
        
        # Создаём summary через DeepSeek
        # ...
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## ДЕТЕКЦИЯ ПЕРЕПОЛНЕНИЯ

### Метод 1: Счётчик сообщений (проактивный)

```python
if self.message_count >= 80:
    raise ChatLimitError("Approaching 100 message limit")
```

**Плюсы**: Предсказуемо
**Минусы**: Не учитывает реальный token usage

### Метод 2: Error Response от DeepSeek (реактивный)

```python
try:
    result = await self._send_message(prompt, **kwargs)
except DeepSeekError as e:
    if "context" in str(e).lower() or "too long" in str(e).lower():
        raise ChatLimitError(f"Chat limit exceeded: {e}")
    raise
```

**Плюсы**: Точный (знаем когда реально переполнен)
**Минусы**: Теряем один turn на ошибку

### РЕКОМЕНДАЦИЯ: Комбинированный

```python
# Проактивная проверка
if self.message_count >= 80:
    logger.warning(f"Chat {self.chat_session_id} near limit ({self.message_count} msgs)")
    raise ChatLimitError("Proactive rotation")

# + Реактивная обработка на случай если пропустили
try:
    result = await self._send_message(...)
except DeepSeekError as e:
    if is_context_error(e):
        raise ChatLimitError(f"Reactive rotation: {e}")
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## SUMMARY

### DeepThink Strategy:

**Рекомендация**: Адаптивная эвристика
- Сложные задачи (debug, design, analysis) → thinking=True
- Простые задачи (create file, delete) → thinking=False
- Default → thinking=True (качество важнее скорости)

### Chat Rotation Strategy:

**Рекомендация**: Proactive rotation с summarization
- Лимит: 80 messages (запас перед 100)
- При достижении → создать summary последних 20 messages
- Создать новый chat_session_id
- Первое сообщение = [System] + [Summary] + New prompt
- Сохранить локально последние 30 messages для summarization

**Результат**:
- ✅ Бесконечные диалоги (gen1 → gen2 → gen3 ...)
- ✅ Контекст сохраняется через summary
- ✅ Прозрачно для пользователя
