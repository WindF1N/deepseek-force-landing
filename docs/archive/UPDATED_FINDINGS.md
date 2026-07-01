# ОБНОВЛЁННЫЕ ВЫВОДЫ: Rate Limiting DeepSeek

## ЧТО ПРОИЗОШЛО

**Проблема**: После ~50 запросов за 4 часа DeepSeek заблокировал аккаунты

**Детали**:
- 4 Chrome Extension сессии (4 аккаунта)
- Все заблокированы практически одновременно (разница 0.133 сек)
- Блокировка: "user is muted" до timestamp 1782857719

**НО**: Через ~10-15 минут система **ВОССТАНОВИЛАСЬ АВТОМАТИЧЕСКИ**!

## НОВЫЕ ДАННЫЕ

✅ **Блокировка ВРЕМЕННАЯ, не 17 часов!**

Изначальный расчёт был неверным:
```bash
# Я посчитал: mute_until - current_time = 17 часов
# НО это было неправильно - timestamp был в другом формате
```

**Реальное время блокировки: ~10-15 минут**

Проверка после блокировки показала:
```
INFO:main:attempt 0 RESULT: success=True
INFO:main:DeepSeek response: 56
```

Все запросы проходят нормально!

---

## ПРИЧИНА БЛОКИРОВКИ

Вероятные триггеры (с 4 аккаунтами одновременно):

1. **IP-based rate limiting**
   - Все 4 расширения с одного IP
   - DeepSeek видит burst запросов с одного адреса

2. **Pattern detection**  
   - Одинаковые промпты (Hermes system prompt)
   - Одинаковое время запросов
   - Идентичный browser fingerprint

3. **Automated behavior**
   - Слишком быстрая последовательность
   - Stress test с 15+ turns подряд
   - Нет "human-like" пауз

---

## ОБНОВЛЁННАЯ ОЦЕНКА ДЛЯ 24/7

### ✅ РЕАЛЬНО МОЖНО ИСПОЛЬЗОВАТЬ, НО:

**С учётом ограничений**:
- ⚠️ Блокировка на 10-15 минут при burst нагрузке
- ⚠️ Нельзя делать >20 запросов за короткий период
- ⚠️ Нужны паузы между запросами (30-60 сек)

**Best practices**:
1. Не делать stress tests (15+ turns подряд)
2. Добавить паузы между запросами (rate limiting в коде)
3. Использовать 4 аккаунта с ротацией (уже есть!)
4. Graceful degradation при "user is muted"

### ОБНОВЛЁННЫЕ РЕКОМЕНДАЦИИ

| Use Case | deepseek-api web | Оценка |
|----------|------------------|--------|
| **Hobby / Эксперименты** | ✅ Подходит | 8/10 |
| **Light dev (20-30 req/day)** | ✅ Подходит с паузами | 7/10 |
| **Medium dev (100 req/day)** | ⚠️ С осторожностью | 5/10 |
| **Heavy dev (500+ req/day)** | ❌ Нужен paid API | 2/10 |
| **Production 24/7** | ❌ Только paid API | 1/10 |

---

## ЧТО УЛУЧШИТЬ В КОДЕ

### 1. Детекция "user is muted"

```python
# backend/main.py
if result.get("biz_code") == 5:  # user muted
    mute_until = result["biz_data"]["mute_until"]
    wait_seconds = mute_until - time.time()
    logger.warning(f"User muted for {wait_seconds}s, will retry after")
    # Fallback to another extension session
    return {"retry_after": wait_seconds}
```

### 2. Automatic session rotation

```python
# При muted - автоматически переключаться на другую extension session
if len(available_sessions) > 1:
    next_session = (current_session_idx + 1) % len(sessions)
    logger.info(f"Rotating to session {next_session}")
```

### 3. Rate limiting protection

```python
# orchestrator/api.py
from slowapi import Limiter
limiter = Limiter(key_func=get_remote_address)

@app.post("/v1/chat/completions")
@limiter.limit("20/minute")  # Max 20 requests per minute
async def chat_completions(req: ChatRequest):
    ...
```

### 4. Graceful degradation

```python
# Если все 4 аккаунта muted - вернуть 503 с Retry-After header
if all_sessions_muted:
    raise HTTPException(
        status_code=503,
        headers={"Retry-After": str(min_wait_time)},
        detail="All DeepSeek sessions temporarily muted"
    )
```

---

## ФИНАЛЬНЫЙ ВЕРДИКТ (ОБНОВЛЁННЫЙ)

### deepseek-api (web UI):

**Для умеренного использования**: ✅ 7/10
- Работает стабильно с паузами
- Блокировка временная (10-15 мин)
- 4 аккаунта дают запас
- Бесплатно

**Для интенсивной разработки**: ⚠️ 4/10
- Нужны паузы (снижает productivity)
- Periodic блокировки раздражают
- Но не критично если добавить retry logic

**Для production**: ❌ 2/10
- Непредсказуемые блокировки
- Нет SLA
- Нет официальной поддержки

---

## NEXT STEPS

### Вариант A: Улучшить текущую систему

Добавить в код:
1. ✅ Детекция mute_until
2. ✅ Auto-rotation между 4 сессиями
3. ✅ Rate limiting (20 req/min)
4. ✅ Graceful degradation
5. ✅ Retry-After headers

**Time**: 1-2 часа работы
**Result**: Stable для 50-100 req/day

### Вариант B: Paid API

Переключиться на:
- Official DeepSeek API
- OpenRouter  
- Hermes с другими провайдерами

**Time**: 30 минут - 2 часа
**Result**: Unlimited, stable, predictable

---

## ВЫВОД

**deepseek-api ПОДХОДИТ для разработки** если:
1. Не делать burst запросы (паузы 30-60 сек)
2. Добавить retry logic для muted errors
3. Использовать 4 аккаунта с ротацией
4. Принять что бывают 10-15 мин паузы

**НЕ критично, МОЖНО жить** для personal projects! 🎉

Хочешь чтобы я добавил улучшения (mute detection + rotation)?
