# Текущее состояние deepseek-api (снимок)

## Что это
Связка превращает бесплатный веб-DeepSeek в OpenAI-совместимый агентный
провайдер для Hermes. Поток:
Hermes → Orchestrator(8002) → Bridge(8001) → Chrome Extension → chat.deepseek.com

## Живые сервисы
- Bridge: docker `deepseek-api-backend`, :8001, health=ok, extension_connected=true
- Postgres: docker, :5434 (bridge историю не пишет — наследие)
- Orchestrator: python :8002, default_mode=tool_loop, VOTE_N=1
- Не git-репозиторий. venv = ./venv (py3.9)

## Модули
- backend/ — чистый прокси: main.py + ws_server.py (SessionPool, честная очередь
  на asyncio.Condition, retry на пустой ответ)
- orchestrator/ — prompt.py, toolcall.py, validator.py(голосование), bigfile.py,
  planner/executor/aggregator (deep), api.py
- Режимы: expert основной, thinking/search ВЫКЛ, vision ВКЛ

## Тесты
- backend: 4/4. orchestrator: 24/24 ТОЛЬКО из orchestrator/ (../venv/bin/python tests/test_orchestrator.py)
- sse_parser: ALL PASSED

## Известные проблемы / техдолг
1. config.py коллизия: backend и orchestrator оба 'config' → pytest из корня
   падает MAX_PARALLEL_SESSIONS. Гонять тесты из orchestrator/.
2. content.js: dliq-токен захардкожен 3x и протухает → vision ломается;
   authToken читается один раз; один WS_AUTH_KEY на всех.
3. README.md/COMMERCIAL.md устарели (Telegram-бот, PG-история удалены из кода).
4. Для 200 юзеров нет: Redis-реестр, per-user auth/rate-limit, headless кластер,
   прокси-ротация. thinking-рассинхрон уже исправлен.

## Доки
README(старый), BRIDGE_HERMES_MERGE_PLAN.md(v3.0 цель), SCALING_ROADMAP.md(200 юзеров),
COMMERCIAL.md(продажный).
