# DEEPSEEK RESPONSE VERIFICATION & VALIDATION

## ПРОБЛЕМА: Что может пойти не так?

DeepSeek может:
- ❌ Неправильно вызвать tool (несуществующий tool_name)
- ❌ Невалидные аргументы (missing required, wrong type)
- ❌ Галлюцинировать (выдумать файлы, команды, результаты)
- ❌ Бесконечный цикл tool calls
- ❌ Ignore tool results (не учитывать ошибки)
- ❌ Невалидный JSON в tool_calls

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## ЧТО ЗНАЧИТ "ВЕРИФИКАЦИЯ"?

### Уровень 1: Syntax Validation (синтаксис)

**Проверяем формат ответа от DeepSeek:**

```python
def validate_deepseek_response(response: dict) -> ValidationResult:
    """
    Проверка синтаксиса ответа от DeepSeek.
    """
    errors = []
    
    # 1. Есть ли content?
    if "content" not in response:
        errors.append("Missing 'content' field")
    
    # 2. Если есть tool_calls - валидный JSON?
    if "tool_calls" in response.get("content", ""):
        try:
            tool_calls = extract_tool_calls(response["content"])
            for tc in tool_calls:
                # Проверяем структуру
                if "name" not in tc:
                    errors.append(f"Tool call missing 'name': {tc}")
                if "arguments" not in tc:
                    errors.append(f"Tool call missing 'arguments': {tc}")
                
                # Валидный JSON в arguments?
                try:
                    json.loads(tc["arguments"])
                except json.JSONDecodeError as e:
                    errors.append(f"Invalid JSON in tool call {tc['name']}: {e}")
        
        except Exception as e:
            errors.append(f"Failed to parse tool_calls: {e}")
    
    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors
    )
```

**Примеры:**

```python
# ✅ Валидный ответ
{
  "content": '{"tool_calls": [{"name": "write_file", "arguments": "{\\"path\\": \\"/tmp/test.txt\\", \\"content\\": \\"hello\\"}"}]}'
}

# ❌ Невалидный - битый JSON
{
  "content": '{"tool_calls": [{"name": "write_file", "arguments": "{path: /tmp/test.txt}"}]}'
}
#                                                                   ↑ нет кавычек!

# ❌ Невалидный - missing fields
{
  "content": '{"tool_calls": [{"name": "write_file"}]}'
}
#                                                    ↑ нет arguments!
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Уровень 2: Schema Validation (схема)

**Проверяем соответствие Hermes tool schema:**

```python
# Hermes tools schema
TOOL_SCHEMAS = {
    "write_file": {
        "required": ["path", "content"],
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "cross_profile": {"type": "boolean", "default": False}
        }
    },
    "read_file": {
        "required": ["path"],
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "default": 1},
            "limit": {"type": "integer", "default": 500}
        }
    },
    "terminal": {
        "required": ["command"],
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer"},
            "background": {"type": "boolean", "default": False}
        }
    }
    # ... остальные tools
}


def validate_tool_call(tool_name: str, arguments: dict) -> ValidationResult:
    """
    Проверка tool call против схемы.
    """
    errors = []
    
    # 1. Существует ли такой tool?
    if tool_name not in TOOL_SCHEMAS:
        errors.append(f"Unknown tool: {tool_name}")
        return ValidationResult(valid=False, errors=errors)
    
    schema = TOOL_SCHEMAS[tool_name]
    
    # 2. Все required поля присутствуют?
    for field in schema["required"]:
        if field not in arguments:
            errors.append(f"Missing required field '{field}' for {tool_name}")
    
    # 3. Типы корректны?
    for field, value in arguments.items():
        if field in schema["properties"]:
            expected_type = schema["properties"][field]["type"]
            actual_type = type(value).__name__
            
            type_map = {
                "string": "str",
                "integer": "int",
                "boolean": "bool",
                "array": "list",
                "object": "dict"
            }
            
            if type_map.get(expected_type) != actual_type:
                errors.append(
                    f"Wrong type for {tool_name}.{field}: "
                    f"expected {expected_type}, got {actual_type}"
                )
    
    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        tool_name=tool_name,
        arguments=arguments
    )
```

**Примеры:**

```python
# ✅ Валидный
validate_tool_call("write_file", {
    "path": "/tmp/test.txt",
    "content": "hello"
})
→ ValidationResult(valid=True, errors=[])

# ❌ Missing required field
validate_tool_call("write_file", {
    "path": "/tmp/test.txt"
    # content отсутствует!
})
→ ValidationResult(valid=False, errors=["Missing required field 'content'"])

# ❌ Wrong type
validate_tool_call("read_file", {
    "path": "/tmp/test.txt",
    "limit": "500"  # строка вместо int!
})
→ ValidationResult(valid=False, errors=["Wrong type for read_file.limit: expected integer, got str"])

# ❌ Unknown tool
validate_tool_call("make_coffee", {"type": "espresso"})
→ ValidationResult(valid=False, errors=["Unknown tool: make_coffee"])
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Уровень 3: Semantic Validation (семантика)

**Проверяем логические проблемы:**

```python
def validate_semantic(tool_name: str, arguments: dict, context: dict) -> ValidationResult:
    """
    Проверка логических проблем в tool call.
    """
    errors = []
    warnings = []
    
    # 1. Dangerous operations
    if tool_name == "terminal":
        cmd = arguments["command"]
        
        # Деструктивные команды без подтверждения
        dangerous_patterns = ["rm -rf /", "dd if=", "mkfs", "> /dev/"]
        if any(p in cmd for p in dangerous_patterns):
            errors.append(f"Dangerous command blocked: {cmd}")
        
        # Подозрительные паттерны
        suspicious = ["curl | bash", "wget | sh", "eval $("]
        if any(p in cmd for p in suspicious):
            warnings.append(f"Suspicious command: {cmd}")
    
    # 2. Path validation
    if tool_name in ["write_file", "read_file", "patch"]:
        path = arguments.get("path", "")
        
        # Абсолютные пути вне home/tmp
        if path.startswith("/") and not (
            path.startswith("/tmp/") or 
            path.startswith("/Users/") or
            path.startswith("/home/")
        ):
            warnings.append(f"Writing outside user directories: {path}")
        
        # Попытка записи в system directories
        system_dirs = ["/etc/", "/var/", "/usr/", "/bin/", "/sbin/"]
        if any(path.startswith(d) for d in system_dirs):
            errors.append(f"Blocked write to system directory: {path}")
    
    # 3. Loop detection
    if context.get("consecutive_same_tool", 0) > 5:
        errors.append(
            f"Possible infinite loop: {tool_name} called "
            f"{context['consecutive_same_tool']} times in a row"
        )
    
    # 4. Resource limits
    if tool_name == "write_file":
        content = arguments.get("content", "")
        if len(content) > 10_000_000:  # 10MB
            errors.append(f"File too large: {len(content)} bytes")
    
    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )
```

**Примеры:**

```python
# ❌ Dangerous command
validate_semantic("terminal", 
    {"command": "rm -rf / --no-preserve-root"},
    {}
)
→ errors=["Dangerous command blocked: rm -rf /"]

# ⚠️ Suspicious but allowed
validate_semantic("terminal",
    {"command": "curl https://install.sh | bash"},
    {}
)
→ warnings=["Suspicious command: curl https://install.sh | bash"]

# ❌ System directory
validate_semantic("write_file",
    {"path": "/etc/hosts", "content": "..."},
    {}
)
→ errors=["Blocked write to system directory: /etc/hosts"]

# ❌ Infinite loop
validate_semantic("read_file",
    {"path": "/tmp/test.txt"},
    {"consecutive_same_tool": 6}
)
→ errors=["Possible infinite loop: read_file called 6 times in a row"]
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## СТРАТЕГИИ ИСПРАВЛЕНИЯ

### Стратегия 1: Error Feedback Loop

**Отправляем ошибку обратно в DeepSeek чат:**

```python
async def execute_with_validation(
    primary_chat: DeepSeekChat,
    tool_name: str,
    arguments: dict,
    context: dict
) -> dict:
    """
    Выполнить tool с validation и error feedback.
    """
    
    # 1. Schema validation
    validation = validate_tool_call(tool_name, arguments)
    
    if not validation.valid:
        # Отправляем ошибку в чат
        error_msg = f"""
Tool call error:
Tool: {tool_name}
Arguments: {json.dumps(arguments, indent=2)}

Validation errors:
{chr(10).join(f"  - {e}" for e in validation.errors)}

Please fix the tool call and try again.
"""
        
        # DeepSeek получит ошибку и исправит
        response = await primary_chat.send(
            prompt=error_msg,
            thinking_enabled=True  # думаем как исправить!
        )
        
        # Парсим исправленный tool call
        fixed_tool_calls = extract_tool_calls(response)
        if fixed_tool_calls:
            # Рекурсивно пробуем снова (с лимитом!)
            if context.get("retry_count", 0) < 3:
                context["retry_count"] = context.get("retry_count", 0) + 1
                return await execute_with_validation(
                    primary_chat,
                    fixed_tool_calls[0]["name"],
                    json.loads(fixed_tool_calls[0]["arguments"]),
                    context
                )
        
        raise ToolValidationError(f"Failed after {context.get('retry_count', 0)} retries")
    
    # 2. Semantic validation
    semantic = validate_semantic(tool_name, arguments, context)
    
    if not semantic.valid:
        error_msg = f"""
Tool call blocked for safety:
{chr(10).join(f"  - {e}" for e in semantic.errors)}

Please use a safer approach.
"""
        response = await primary_chat.send(error_msg, thinking_enabled=True)
        # ... retry logic
    
    # 3. Всё ОК - выполняем
    result = await execute_tool(tool_name, arguments)
    
    return result
```

**Пример flow:**

```
Turn 1:
Agent: write_file({"path": "/tmp/test.txt"})  ← missing content!

Validator: ❌ Missing required field 'content'

Turn 2 (auto):
System → Agent: "Tool call error: Missing required field 'content'. Please fix."
Agent: write_file({"path": "/tmp/test.txt", "content": "hello"})  ← исправил!

Validator: ✅ OK

Tool executed: File created ✅
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Стратегия 2: Auto-Fix (простые ошибки)

**Автоматически исправляем некоторые проблемы:**

```python
def auto_fix_tool_call(tool_name: str, arguments: dict) -> dict:
    """
    Попытка автоматического исправления простых ошибок.
    """
    fixed = arguments.copy()
    schema = TOOL_SCHEMAS.get(tool_name)
    
    if not schema:
        return fixed
    
    # 1. Добавить defaults для optional параметров
    for field, props in schema["properties"].items():
        if field not in fixed and "default" in props:
            fixed[field] = props["default"]
    
    # 2. Type coercion (строка → int, и т.д.)
    for field, value in fixed.items():
        if field in schema["properties"]:
            expected = schema["properties"][field]["type"]
            
            if expected == "integer" and isinstance(value, str):
                try:
                    fixed[field] = int(value)
                except ValueError:
                    pass
            
            elif expected == "boolean" and isinstance(value, str):
                fixed[field] = value.lower() in ["true", "1", "yes"]
    
    # 3. Path normalization
    if tool_name in ["write_file", "read_file", "patch"]:
        if "path" in fixed:
            # Expand ~
            fixed["path"] = os.path.expanduser(fixed["path"])
            
            # Относительные пути → абсолютные
            if not fixed["path"].startswith("/"):
                fixed["path"] = os.path.abspath(fixed["path"])
    
    return fixed
```

**Примеры:**

```python
# Auto-fix: добавить default
auto_fix_tool_call("read_file", {"path": "/tmp/test.txt"})
→ {"path": "/tmp/test.txt", "offset": 1, "limit": 500}

# Auto-fix: type coercion
auto_fix_tool_call("read_file", {"path": "/tmp/test.txt", "limit": "100"})
→ {"path": "/tmp/test.txt", "limit": 100, "offset": 1}

# Auto-fix: path expansion
auto_fix_tool_call("write_file", {"path": "~/test.txt", "content": "hello"})
→ {"path": "/Users/vergilobj/test.txt", "content": "hello"}
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Стратегия 3: Circuit Breaker (бесконечные циклы)

**Останавливаем бесконечные циклы:**

```python
class CircuitBreaker:
    def __init__(self):
        self.tool_sequence = []  # последние N tool calls
        self.max_same_tool = 5
        self.max_sequence_repeats = 3
    
    def check(self, tool_name: str) -> bool:
        """
        Returns True если можно выполнять, False если circuit open.
        """
        self.tool_sequence.append(tool_name)
        
        # Последние 10 calls
        recent = self.tool_sequence[-10:]
        
        # 1. Один и тот же tool > 5 раз подряд?
        consecutive = 1
        for i in range(len(recent) - 1, 0, -1):
            if recent[i] == recent[i-1]:
                consecutive += 1
            else:
                break
        
        if consecutive > self.max_same_tool:
            logger.error(
                f"Circuit breaker: {tool_name} called "
                f"{consecutive} times consecutively"
            )
            return False
        
        # 2. Повторяющаяся последовательность?
        # Например: [A, B, A, B, A, B] → паттерн!
        if len(recent) >= 6:
            pattern_len = 2
            pattern = recent[-pattern_len*2:-pattern_len]
            last = recent[-pattern_len:]
            
            if pattern == last:
                # Проверяем ещё раз назад
                prev = recent[-pattern_len*3:-pattern_len*2]
                if prev == pattern:
                    logger.error(
                        f"Circuit breaker: repeating pattern "
                        f"{pattern} detected"
                    )
                    return False
        
        return True
    
    def trip(self, reason: str):
        """
        Открыть circuit (остановить выполнение).
        """
        raise CircuitBreakerError(
            f"Circuit breaker tripped: {reason}\n"
            f"Recent tool calls: {self.tool_sequence[-10:]}"
        )


# Usage
circuit_breaker = CircuitBreaker()

async def execute_tool_safe(tool_name: str, arguments: dict):
    if not circuit_breaker.check(tool_name):
        circuit_breaker.trip(f"Too many {tool_name} calls")
    
    return await execute_tool(tool_name, arguments)
```

**Примеры:**

```python
# ❌ Бесконечный цикл - одинаковый tool
calls = ["read_file"] * 6
for tool in calls:
    circuit_breaker.check(tool)
# → После 6-го: Circuit breaker tripped: Too many read_file calls

# ❌ Повторяющийся паттерн
calls = ["read_file", "write_file"] * 4
for tool in calls:
    circuit_breaker.check(tool)
# → После 4-го repeat: Circuit breaker tripped: repeating pattern detected

# ✅ Нормальное использование
calls = ["read_file", "patch", "terminal", "read_file"]
for tool in calls:
    circuit_breaker.check(tool)
# → Всё ОК
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## ИНТЕГРАЦИЯ В ORCHESTRATOR

```python
# orchestrator/api.py

class ValidationContext:
    def __init__(self):
        self.retry_count = 0
        self.circuit_breaker = CircuitBreaker()
        self.tool_history = []


async def _run_persistent_chat(req: ChatRequest):
    user_id = generate_user_id(req)
    
    # Validation context для этого user
    if not hasattr(backend, "validation_contexts"):
        backend.validation_contexts = {}
    
    if user_id not in backend.validation_contexts:
        backend.validation_contexts[user_id] = ValidationContext()
    
    val_ctx = backend.validation_contexts[user_id]
    
    # Парсим Hermes messages
    messages = req.messages
    last_msg = messages[-1]
    
    if last_msg.role == "assistant" and last_msg.tool_calls:
        # Агент вызвал tools
        results = []
        
        for tool_call in last_msg.tool_calls:
            tool_name = tool_call.function.name
            
            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as e:
                # Syntax error в JSON
                error_result = {
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "content": f"JSON parse error: {e}\nPlease fix the JSON format."
                }
                
                # Отправляем ошибку обратно в чат
                primary_chat = await backend.get_primary_chat(user_id)
                await primary_chat.send(
                    prompt=error_result["content"],
                    thinking_enabled=True
                )
                
                return format_error_response(error_result)
            
            # === VALIDATION PIPELINE ===
            
            # 1. Schema validation
            schema_result = validate_tool_call(tool_name, arguments)
            if not schema_result.valid:
                if val_ctx.retry_count >= 3:
                    return format_error_response({
                        "error": "Max retries exceeded",
                        "errors": schema_result.errors
                    })
                
                # Feedback loop
                val_ctx.retry_count += 1
                
                primary_chat = await backend.get_primary_chat(user_id)
                error_msg = format_validation_error(schema_result)
                
                await primary_chat.send(error_msg, thinking_enabled=True)
                continue
            
            # 2. Auto-fix
            fixed_args = auto_fix_tool_call(tool_name, arguments)
            
            # 3. Semantic validation
            semantic_result = validate_semantic(
                tool_name, 
                fixed_args,
                {"consecutive_same_tool": val_ctx.tool_history.count(tool_name)}
            )
            
            if not semantic_result.valid:
                # Blocked for safety
                primary_chat = await backend.get_primary_chat(user_id)
                error_msg = f"Tool call blocked: {semantic_result.errors}"
                await primary_chat.send(error_msg, thinking_enabled=True)
                continue
            
            # 4. Circuit breaker
            if not val_ctx.circuit_breaker.check(tool_name):
                return format_error_response({
                    "error": "Circuit breaker tripped",
                    "tool": tool_name,
                    "history": val_ctx.tool_history[-10:]
                })
            
            # === EXECUTION ===
            
            # Vision/Search перехват
            if tool_name == "browser_vision":
                result = await execute_vision_helper(user_id, fixed_args)
            elif tool_name == "web_search":
                result = await execute_search_helper(user_id, fixed_args)
            else:
                # Обычный tool
                result = await execute_tool(tool_name, fixed_args)
            
            # Success - reset retry counter
            val_ctx.retry_count = 0
            val_ctx.tool_history.append(tool_name)
            
            results.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "content": json.dumps(result)
            })
        
        return format_tool_results(results)
    
    else:
        # User message - обычная отправка
        primary_chat = await backend.get_primary_chat(user_id)
        
        prompt = format_prompt(last_msg, primary_chat)
        response = await primary_chat.send(
            prompt=prompt,
            thinking_enabled=True  # hardcoded пока
        )
        
        return format_response(response)
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## SUMMARY: ЧТО ЗНАЧИТ "ВЕРИФИКАЦИЯ"?

### 3 уровня проверки:

1. **Syntax Validation** (синтаксис)
   - Валидный JSON?
   - Есть ли обязательные поля?
   - → Auto-fix или error feedback

2. **Schema Validation** (схема)
   - Существует ли tool?
   - Правильные типы параметров?
   - Все required поля?
   - → Auto-fix или error feedback

3. **Semantic Validation** (семантика)
   - Безопасная операция?
   - Логические проблемы?
   - Бесконечный цикл?
   - → Block или circuit breaker

### Стратегии исправления:

1. **Error Feedback Loop**
   - Отправить ошибку в DeepSeek чат
   - Модель исправляет сама
   - Max 3 retry

2. **Auto-Fix**
   - Добавить defaults
   - Type coercion
   - Path normalization

3. **Circuit Breaker**
   - Остановить бесконечные циклы
   - Защита от зависаний

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## РЕЗУЛЬТАТ

**DeepSeek галлюцинирует** → Validator ловит → Error feedback → DeepSeek исправляет → Success! 🎯

**Бесконечный цикл** → Circuit breaker → Stop → Error to user

**Dangerous command** → Semantic validator → Blocked → Safe alternative suggested
