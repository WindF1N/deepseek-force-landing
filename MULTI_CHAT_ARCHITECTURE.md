# PERSISTENT CHAT: Multi-Chat Architecture

## КЛЮЧЕВОЕ ОГРАНИЧЕНИЕ DeepSeek

**НЕЛЬЗЯ менять model_type внутри одного чата!**

```
❌ НЕПРАВИЛЬНО:
chat_session_id = "abc123"
├── msg_001: model_type=None (обычный)
├── msg_002: model_type="vision" ← DeepSeek отклонит!
└── msg_003: model_type=None

✅ ПРАВИЛЬНО:
chat_session_id = "abc123" (основной агент)
├── msg_001: model_type=None
├── msg_002: model_type=None
└── msg_003: model_type=None

chat_session_id = "xyz789" (vision helper)
├── msg_001: model_type="vision"
└── msg_002: model_type="vision"

chat_session_id = "def456" (search helper)
├── msg_001: search_enabled=True
└── msg_002: search_enabled=True
```

**Можно toggle внутри чата**: `thinking_enabled` (deepthink on/off)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## АРХИТЕКТУРА: 3 ТИПА ЧАТОВ

```
┌─────────────────────────────────────────────────────────────┐
│ USER CHAT CONTEXT (user_id = "abc123")                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [1] PRIMARY CHAT (основной агент)                         │
│      chat_session_id: "main_abc123"                        │
│      model_type: None (default)                            │
│      thinking_enabled: True/False (можно toggle!)          │
│      ├─ msg_001: User: "создай сайт"                       │
│      ├─ msg_002: Agent: write_file(...)                    │
│      ├─ msg_003: Tool result: "Created"                    │
│      ├─ msg_004: Agent: "проверю визуально"                │
│      ├─ msg_005: Vision result: "Сайт выглядит..."         │
│      └─ msg_006: Agent: "Готово!"                          │
│                                                             │
│  [2] VISION CHAT (helper для vision)                       │
│      chat_session_id: "vision_abc123"                      │
│      model_type: "vision"                                  │
│      thinking_enabled: False                               │
│      └─ msg_001: "Describe this image: <base64>"           │
│          Response: "I see an apple website..."             │
│                                                             │
│  [3] SEARCH CHAT (helper для search)                       │
│      chat_session_id: "search_abc123"                      │
│      search_enabled: True                                  │
│      thinking_enabled: False                               │
│      └─ msg_001: "Find latest news about AI"               │
│          Response: "Found 5 results..."                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## FLOW: Как Работает Vision

### Шаг 1: Агент решает использовать vision

```python
# В основном чате агент вызвал vision tool
primary_chat.send("""
  User: создай сайт про яблоки
""")
→ Agent response: "write_file(...) then browser_vision(...)"
```

### Шаг 2: Orchestrator перехватывает vision tool call

```python
# orchestrator/api.py
if tool_name == "browser_vision":
    # НЕ выполняем в основном чате!
    # Создаём ОТДЕЛЬНЫЙ vision чат
    
    vision_result = await _run_vision_helper(
        user_id=user_id,
        image_url=tool_args["image_url"],
        question=tool_args["question"]
    )
    
    # vision_result = "I see an apple website with..."
```

### Шаг 3: Vision helper чат (отдельный!)

```python
async def _run_vision_helper(user_id, image_url, question):
    # Получаем/создаём vision чат
    ctx = await backend.get_vision_chat(user_id)
    # → chat_session_id = "vision_abc123"
    
    # Отправляем в vision чат
    result = await ctx.send(
        prompt=f"Question: {question}",
        model_type="vision",
        images=[image_url]
    )
    
    return result["content"]
```

### Шаг 4: Результат возвращается в основной чат

```python
# Возвращаем в основной чат как tool result
primary_chat.send(f"""
  Tool result (browser_vision):
  {vision_result}
""")
→ Agent: "Сайт выглядит отлично! Готово."
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## РЕАЛЬНЫЙ ПРИМЕР: "Создай сайт и проверь"

### Primary Chat (основной агент):

```
msg_001: [40K system] + User: "создай сайт про яблоки и проверь визуально"
  → Agent: "Создам сайт"
  → Tool call: write_file(apple-shop/index.html)

msg_002: Tool result: "Created apple-shop/index.html (1667 lines)"
  → Agent: "Файл создан, сейчас открою в браузере"
  → Tool call: browser_navigate(file://...)

msg_003: Tool result: "Page loaded, title: AppleShop"
  → Agent: "Страница открыта, проверю визуально"
  → Tool call: browser_vision(question="How does the site look?")
  
  [ЗДЕСЬ orchestrator перехватывает и запускает vision helper]

msg_004: Tool result (browser_vision): "I see a modern apple website with a hero section, product cards, green theme, responsive layout"
  → Agent: "Сайт выглядит отлично! Все элементы на месте. Готово!"
```

### Vision Chat (helper, отдельный):

```
chat_session_id = "vision_abc123"
model_type = "vision"

msg_001: prompt="Describe this website screenshot"
         images=[<base64 of screenshot>]
  → DeepSeek Vision: "I see a modern apple website with hero section..."
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## FLOW: Как Работает Search

### Пример: "Найди новости про AI и напиши summary"

### Primary Chat:

```
msg_001: User: "найди новости про AI и напиши summary"
  → Agent: "Поищу новости"
  → Tool call: web_search(query="AI news")
  
  [orchestrator перехватывает и запускает search helper]

msg_002: Tool result (web_search): 
  "1. OpenAI released GPT-5... 
   2. Google announces Gemini 2.0...
   3. Anthropic..."
  → Agent: "Вот summary: В последние дни..."
```

### Search Chat (helper, отдельный):

```
chat_session_id = "search_abc123"
search_enabled = True

msg_001: prompt="Find latest AI news"
  → DeepSeek Search: "1. OpenAI GPT-5... 2. Google Gemini..."
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## СТРУКТУРА ДАННЫХ

### UserChatContext Class:

```python
class UserChatContext:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.created_at = time.time()
        
        # Основной агентский чат
        self.primary_chat = None  # DeepSeekChat
        
        # Helper чаты (создаются по требованию)
        self.vision_chat = None   # DeepSeekChat with model_type="vision"
        self.search_chat = None   # DeepSeekChat with search_enabled=True
```

### DeepSeekChat Class:

```python
class DeepSeekChat:
    def __init__(self, chat_session_id: str, session, 
                 mode: str = "default"):
        self.chat_session_id = chat_session_id
        self.parent_message_id = None
        self.session = session
        self.mode = mode  # "default" | "vision" | "search"
    
    async def send(self, prompt: str, 
                   model_type: str = None,
                   search_enabled: bool = False,
                   thinking_enabled: bool = True,
                   images: list = None) -> dict:
        """
        model_type: None (default) | "vision" | "expert"
        search_enabled: True/False
        thinking_enabled: True/False (можно toggle!)
        """
        payload = {
            "action": "continue_chat",
            "chat_session_id": self.chat_session_id,
            "parent_message_id": self.parent_message_id,
            "prompt": prompt,
            "model_type": model_type,
            "search_enabled": search_enabled,
            "thinking_enabled": thinking_enabled,
            "images": images or []
        }
        
        result = await self.session.send_request(payload)
        
        # Обновляем parent для следующего message
        self.parent_message_id = result["response_message_id"]
        
        return result
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## КАК СОЗДАЮТСЯ HELPER ЧАТЫ?

### Vision Chat Creation (lazy):

```python
class ChatContextManager:
    async def get_vision_chat(self, user_id: str):
        ctx = await self.get_or_create(user_id)
        
        if ctx.vision_chat is None:
            # Создаём новый vision чат
            result = await self.pool.send_request({
                "action": "create_chat_session"
            })
            vision_session_id = result["chat_session_id"]
            
            # Создаём DeepSeekChat для vision
            session = await self.pool.get_session()
            ctx.vision_chat = DeepSeekChat(
                vision_session_id, 
                session, 
                mode="vision"
            )
        
        return ctx.vision_chat
```

### Search Chat Creation (lazy):

```python
    async def get_search_chat(self, user_id: str):
        ctx = await self.get_or_create(user_id)
        
        if ctx.search_chat is None:
            result = await self.pool.send_request({
                "action": "create_chat_session"
            })
            search_session_id = result["chat_session_id"]
            
            session = await self.pool.get_session()
            ctx.search_chat = DeepSeekChat(
                search_session_id,
                session,
                mode="search"
            )
        
        return ctx.search_chat
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## ORCHESTRATOR LOGIC

```python
# orchestrator/api.py

async def _run_persistent_chat(req: ChatRequest):
    user_id = generate_user_id(req)
    
    # Парсим Hermes messages
    messages = req.messages
    last_msg = messages[-1]
    
    # Проверяем: это tool call от агента или user message?
    if last_msg.role == "assistant" and last_msg.tool_calls:
        # Агент вызвал tools
        for tool_call in last_msg.tool_calls:
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)
            
            if tool_name == "browser_vision":
                # Vision в отдельном чате!
                vision_chat = await backend.get_vision_chat(user_id)
                result = await vision_chat.send(
                    prompt=tool_args["question"],
                    model_type="vision",
                    images=[tool_args["image_url"]]
                )
                
                # Результат возвращаем в primary chat
                # (будет отправлен на следующем turn)
                return format_tool_result(tool_call.id, result)
            
            elif tool_name == "web_search":
                # Search в отдельном чате!
                search_chat = await backend.get_search_chat(user_id)
                result = await search_chat.send(
                    prompt=tool_args["query"],
                    search_enabled=True
                )
                
                return format_tool_result(tool_call.id, result)
            
            else:
                # Обычный tool - выполняем локально
                result = await execute_tool(tool_name, tool_args)
                return format_tool_result(tool_call.id, result)
    
    elif last_msg.role == "tool":
        # Tool result от Hermes - отправляем в primary chat
        primary_chat = await backend.get_primary_chat(user_id)
        
        tool_result_text = f"Tool result: {last_msg.content}"
        response = await primary_chat.send(
            prompt=tool_result_text,
            thinking_enabled=True  # можно toggle!
        )
        
        return format_response(response)
    
    else:
        # User message - отправляем в primary chat
        primary_chat = await backend.get_primary_chat(user_id)
        
        # Первый turn? Добавляем system instructions
        if primary_chat.parent_message_id is None:
            full_prompt = f"{SYSTEM_INSTRUCTIONS}\n\nUser: {last_msg.content}"
        else:
            full_prompt = f"User: {last_msg.content}"
        
        response = await primary_chat.send(
            prompt=full_prompt,
            thinking_enabled=True
        )
        
        return format_response(response)
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## DEEPTHINK TOGGLE (можно внутри чата!)

```python
# Primary chat может toggle thinking_enabled
primary_chat.send(prompt="...", thinking_enabled=True)   # deepthink ON
primary_chat.send(prompt="...", thinking_enabled=False)  # deepthink OFF
primary_chat.send(prompt="...", thinking_enabled=True)   # deepthink ON again

# ✅ ЭТО РАБОТАЕТ! DeepSeek разрешает toggle thinking внутри чата
```

**НО:**

```python
# ❌ НЕЛЬЗЯ toggle model_type!
primary_chat.send(prompt="...", model_type=None)
primary_chat.send(prompt="...", model_type="vision")  # ← ОШИБКА!
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## ПРЕИМУЩЕСТВА MULTI-CHAT

✅ **Изоляция режимов**
   - Vision не загрязняет основной контекст
   - Search не мешает логике агента

✅ **Эффективность**
   - Vision чат используется только когда нужно
   - Search чат только для поиска
   - Primary чат остаётся чистым

✅ **Масштабируемость**
   - Можно добавить expert_chat (model_type="expert")
   - Можно добавить code_chat (специализированный)

✅ **Переиспользование**
   - Vision чат живёт весь session
   - Не нужно пересоздавать каждый раз

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## SUMMARY

**3 чата на одного user:**

1. **Primary Chat** (основной агент)
   - model_type: None/default
   - thinking_enabled: toggle по требованию
   - Весь conversation flow

2. **Vision Chat** (helper)
   - model_type: "vision" (фиксированный!)
   - Только image analysis
   - Результат → primary chat

3. **Search Chat** (helper)
   - search_enabled: True (фиксированный!)
   - Только web search
   - Результат → primary chat

**Правило**: model_type нельзя менять, thinking_enabled можно!
