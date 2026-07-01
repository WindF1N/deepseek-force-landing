# DeepSeek Orchestrator (port 8002)

Реализация плана BRIDGE_HERMES_MERGE_PLAN.md v3.0. Промежуточный слой между
Hermes и DeepSeek Bridge (8001). Делает веб-DeepSeek пригодным как агентный
провайдер для Hermes и добавляет конвейер для сложных задач.

## Архитектура
Hermes → Orchestrator (8002) → Bridge (8001) → Chrome Extension → chat.deepseek.com

## Файлы
- config.py        — настройки (порт, VOTE_N, режимы, таймауты)
- bridge_client.py — async-клиент к Bridge + fan_out для голосования
- prompt.py        — рендер OpenAI-запроса Hermes в плоский промпт DeepSeek
- toolcall.py      — нормализация текста DeepSeek в нативные OpenAI tool_calls
- validator.py     — валидация по JSON-схемам + self-consistency голосование + recovery
- planner.py       — декомпозиция задачи в JSON-план (deep mode)
- executor.py      — sequential/parallel/hybrid исполнение шагов
- aggregator.py    — сборка финального ответа (deep mode)
- api.py           — FastAPI на 8002, OpenAI-совместимый

## Два режима
- tool_loop (по умолчанию): прозрачный прокси на каждый ход агентного цикла
  Hermes. Возвращает НАТИВНЫЕ OpenAI tool_calls. Это то, что нужно Hermes.
- deep: Planner→Executor→Validator→Aggregator для одной сложной задачи.
  Включается моделью с суффиксом "-deep" или заголовком X-Orch-Mode: deep.

## Запуск
```bash
cd ~/deepseek-api/orchestrator
source ../venv/bin/activate
pip install -r requirements.txt
uvicorn api:app --host 0.0.0.0 --port 8002
```
Требуется запущенный Bridge (docker compose up -d backend) и хотя бы одно окно
Chrome с расширением на chat.deepseek.com.

## Подключение к Hermes
Отдельный профиль, чтобы не трогать основной Claude:
```bash
hermes profile create deepseek --clone
hermes -p deepseek config set model.provider custom
hermes -p deepseek config set model.default deepseek-chat
hermes -p deepseek config set model.base_url http://localhost:8002/v1
hermes -p deepseek config set model.api_key supersecretkey
hermes -p deepseek chat -q "..." --yolo
```

## Переменные окружения
- VOTE_N=3            — self-consistency: N параллельных вызовов + голосование
                        (нужно N окон Chrome, иначе очередь на одной сессии)
- ORCH_MODE=deep      — режим по умолчанию
- MAX_RECOVERY=3      — попыток восстановления при невалидном ответе
- MAX_PARALLEL_SESSIONS=5

## Известные ограничения
- Одно окно Chrome = одна сессия. Параллелизм/голосование требуют нескольких
  окон (разные профили Chrome). Иначе fan_out просто стоит в очереди и даёт 500
  при перегрузе.
- Латентность ~8-12с на вызов; deep-режим — минуты.
- search_enabled выключен для deepseek-chat (иначе ломает протокол tool-calls;
  включить через модель с "search" в имени).
- dliq-токен в content.js захардкожен и протухает — vision/файлы периодически
  ломаются.
