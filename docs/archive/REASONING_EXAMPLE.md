# Reasoning в tool_calls — как это работает

## Проблема

Раньше мы теряли пояснения/reasoning, которые DeepSeek давал вместе с tool_calls:

```
DeepSeek ответ:
"I need to check if the file exists first, then read its contents.

{"tool_calls": [{"name": "read_file", "arguments": {"path": "/tmp/config.json"}}]}"
```

**Старый результат** (reasoning терялся):
```json
{
  "role": "assistant",
  "content": null,  ❌ ПОТЕРЯН REASONING
  "tool_calls": [...]
}
```

## Решение

Теперь парсер `toolcall.parse()` извлекает **оба компонента**:

1. **Reasoning** — текст ДО JSON блока
2. **Tool calls** — структурированные вызовы

**Новый результат**:
```json
{
  "role": "assistant",
  "content": "I need to check if the file exists first, then read its contents.",
  "tool_calls": [
    {
      "id": "call_abc123",
      "type": "function",
      "function": {
        "name": "read_file",
        "arguments": "{\"path\": \"/tmp/config.json\"}"
      }
    }
  ]
}
```

## Как Hermes это использует

Hermes показывает reasoning пользователю ДО выполнения инструмента:

```
🤖 DeepSeek: I need to check if the file exists first, then read its contents.
┊ 📖 read_file /tmp/config.json
┊ ✓ Success: {"content": "...", "size": 1234}

🤖 DeepSeek: Now I'll update the configuration...
┊ ✍️  write_file /tmp/config.json
┊ ✓ Success: Written 1456 bytes
```

Пользователь **видит мышление модели** перед каждым действием.

## Примеры reasoning

### 1. Планирование шагов
```
DeepSeek: "I'll first list the directory to see what files exist, then read each one."

Tool call: terminal("ls -la /tmp")
```

### 2. Объяснение логики
```
DeepSeek: "The error suggests missing dependency. I'll install it via pip."

Tool call: terminal("pip install requests")
```

### 3. Промежуточные выводы
```
DeepSeek: "Based on the test results, the function works correctly. I'll now create the final implementation."

Tool call: write_file(path="main.py", content="...")
```

### 4. Проверка перед действием
```
DeepSeek: "Let me verify the file path is correct before writing."

Tool call: terminal("pwd")
```

## Форматы, которые поддерживаются

### Чистый JSON с reasoning:
```
I need to do X first.

{"tool_calls": [...]}
```

### Markdown fence с reasoning:
```
Let me check this.

```json
{"tool_calls": [...]}
```
```

### Функциональный стиль с reasoning:
```
This will help understand the structure.

read_file({"path": "/tmp/test.txt"})
```

## Технические детали

### toolcall.py

Функция `_extract_reasoning()` ищет текст ДО первого `{`:

```python
def _extract_reasoning(text: str) -> Optional[str]:
    json_start = text.find('{')
    if json_start == -1:
        return None
    
    before = text[:json_start].strip()
    before = re.sub(r'```(?:json)?', '', before).strip()
    
    if len(before) > 5 and not before.isspace():
        return before
    
    return None
```

### validator.py

Голосование учитывает reasoning — возвращает `content` вместе с `tool_calls`:

```python
if calls:
    return {
        "kind": "tool_calls",
        "tool_calls": calls,
        "content": content,  # ← ДОБАВЛЕНО
        "votes": votes,
        "total": len(raw_answers)
    }
```

### api.py

Передаём reasoning в OpenAI-совместимый ответ:

```python
reasoning = decision.get("content")
return _resp_envelope(req.model, content=reasoning, tool_calls=tcs)
```

## Важно

- Reasoning опциональный — если DeepSeek не дал пояснение, `content` будет `null`
- Hermes выполняет tool, DeepSeek только планирует
- Reasoning показывается пользователю, но НЕ влияет на выполнение
- OpenAI API полностью совместим (оба поля могут быть заполнены одновременно)

## Будущее улучшение: контрольные промпты

В persistent chat можно **запрашивать reasoning явно**:

```
USER: Create a landing page

REMINDER: Before calling a tool, briefly explain your reasoning (1-2 sentences).

DeepSeek: I'll create an HTML file with responsive design and modern styling.
{"tool_calls": [{"name": "write_file", ...}]}
```

Это сделает поведение более предсказуемым и информативным.
