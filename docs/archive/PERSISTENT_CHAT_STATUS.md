# PERSISTENT CHAT: Проблемы и Решения

**Дата**: 2026-06-30  
**Статус**: ❌ НЕ РАБОТАЕТ (WIP)  
**Причина**: Архитектурные проблемы при создании DeepSeek чата

---

## Что Пытались Сделать

**Цель**: Избавиться от отправки огромных промптов (47K chars) каждый раз. Вместо этого:
1. Создать один DeepSeek chat session per Hermes conversation
2. Отправлять только новые сообщения (tool results, user input)
3. DeepSeek сам держит контекст внутри chat_session_id

**Ожидаемый результат**: Промпты 100-500 chars вместо 47K+

---

## Что Реализовали

### Backend (ws_server.py)

✅ **ChatContextManager** - управляет persistent contexts
✅ **DeepSeekChat** - обёртка вокруг одного chat_session_id
✅ **UserChatContext** - привязка user_id → DeepSeekChat
✅ **REST API**: 
   - `GET /api/contexts/{user_id}` - info
   - `POST /api/contexts/{user_id}/send` - send message

### Orchestrator (api.py)

✅ **_run_persistent_chat()** - новый mode
✅ **Генерация user_id** из conversation hash
✅ **Минимальные промпты**: только current turn
✅ **HTTP клиент** для взаимодействия с backend API

### Config

✅ **DEFAULT_MODE**: можно переключать между tool_loop и persistent_chat

---

## Где Застряли

### Проблема: "missing prompt or ref file"

При создании нового chat context:

```python
# Step 1: Create empty chat
result = await pool.send_request({"action": "create_chat_session"})
# ✅ Работает - получаем chat_session_id

# Step 2: Send init message
result = await ctx.primary_chat.send("Ready", model="default")
# ❌ FAILS: "missing prompt or ref file"
```

**DeepSeek API требует** либо prompt, либо ref_file_ids в каждом completion request.

**Chrome Extension** передаёт:
```javascript
{
  chat_session_id,
  parent_message_id,
  prompt: prompt || '',  // Empty string allowed?
  ref_file_ids: [],
  ...
}
```

Но DeepSeek **ОТКЛОНЯЕТ** пустые промпты: `biz_code: 6, biz_msg: "missing prompt or ref file"`

### Проблемная Цепочка

1. Orchestrator вызывает `POST /api/contexts/{user_id}/send` с prompt="Ready"
2. Backend вызывает `ctx.primary_chat.send("Ready")`
3. DeepSeekChat отправляет `continue_chat` action
4. Chrome Extension формирует completion request
5. DeepSeek API возвращает 422: "missing prompt or ref file"

### Гипотезы

1. **Prompt слишком короткий?** - Попробовали "Ready", "Hello! I'm ready.", всё отклоняется
2. **model_type неправильный?** - Передаём "default", extension требует default/expert/vision
3. **Нужны дополнительные поля?** - Может thinking_enabled, search_enabled?
4. **PoW token проблема?** - Extension генерирует PoW, но может что-то не так?
5. **Rate limiting?** - DeepSeek заблокировал? НО старый /v1/chat/completions РАБОТАЕТ!

---

## Verification

**Ad-hoc тест** (не canonical suite):

```bash
# ✅ Old bridge endpoint WORKS
curl -X POST http://localhost:8001/v1/chat/completions \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"42"}]}'
# → Returns full response with reasoning

# ❌ Persistent chat FAILS
curl -X POST http://localhost:8002/v1/chat/completions \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"42"}]}'
# → 500 Internal Server Error
```

**Логи**:
```
RuntimeError: Failed to create chat session: 
Пустой ответ от DeepSeek | 
biz_code:6, biz_msg:"missing prompt or ref file"
```

---

## Текущее Решение

**Откатились на tool_loop mode** (DEFAULT в config.py):

```python
DEFAULT_MODE = "tool_loop"  # Stable, works
```

- ✅ Стабильно работает
- ✅ Tool calls парсятся
- ✅ Vision mode работает
- ❌ Отправляет весь Hermes контекст каждый раз (47K chars)
- ❌ Не масштабируется на длинные диалоги

---

## Что Нужно Доделать

### Вариант 1: Дебаг Current Approach

1. **Логировать raw request** который extension отправляет в DeepSeek API
2. **Сравнить** с working request от старого bridge endpoint
3. **Найти различие** в headers/body/format
4. **Исправить** backend/ws_server.py или content.js

### Вариант 2: Альтернативный Flow

Вместо init message, делать:

```python
# Create chat + send первый реальный prompt сразу
# Не делать dummy "Ready" message
```

Проблема: Нужно переделать get_or_create() чтобы принимал первый prompt.

### Вариант 3: Использовать Tool_Loop Efficiently

Не создавать persistent chat, но:
- **Compressor**: сжимать старые сообщения в summary
- **Sliding window**: отправлять только last N turns
- **Lazy loading tools**: передавать tool definitions только когда нужны

---

## Impact

**Без persistent chat**:
- Средний промпт: 47K chars
- Max turns: ~50 до context overflow
- Token waste: огромный

**С persistent chat** (если бы работало):
- Средний промпт: 200-500 chars (98.5% reduction!)
- Max turns: 200-300+
- Token waste: минимальный

---

## Файлы Изменённые

- ✅ `backend/main.py` - добавлены REST API endpoints
- ✅ `backend/ws_server.py` - ChatContextManager, DeepSeekChat
- ✅ `orchestrator/api.py` - _run_persistent_chat()
- ✅ `orchestrator/config.py` - DEFAULT_MODE toggle
- ✅ `orchestrator/bridge_client.py` - HTTP client методы
- ⚠️ `unified_server.py` - создан но не используется

---

## Заключение

**Архитектура persistent chat спроектирована правильно**, но:
- ❌ Не можем создать первый message в чате
- ❌ DeepSeek API требует что-то специфическое
- ❌ Нет ясности что именно не так

**Рекомендация**:
1. Использовать tool_loop (стабильно) до фикса persistent_chat
2. Изучить Chrome DevTools Network tab чтобы увидеть реальные запросы
3. Сравнить working vs broken requests
4. Вернуться к persistent_chat когда найдём root cause

**Время потрачено**: ~2 часа debugging  
**Осталось работы**: ~1-2 часа (если найдём root cause)

---

**TL;DR**: Persistent chat код написан, но DeepSeek API отклоняет init message.
Откатились на tool_loop. Нужен дальнейший дебаг.
