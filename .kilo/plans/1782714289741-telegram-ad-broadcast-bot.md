# План: Telegram-юзербот для рассылки рекламы (Telethon)

## Цель
Одноразовый CLI-скрипт на Python + Telethon, который рассылает заранее составленное сообщение с прикреплённым медиафайлом по всем групповым чатам аккаунта, где отправка возможна. Проект: `C:\Users\novra\Desktop\reklama` (папка пустая — старт с нуля).

## Решённые решения
- **Платформа/аккаунт:** Telegram, рассылка от имени личного аккаунта через MTProto (Telethon).
- **Источник чатов:** авто-перебор всех диалогов аккаунта.
- **Тип диалогов:** только группы и супергруппы (ЛС и каналы-трансляции без прав админа — исключаются).
- **Содержимое:** текст из `message.txt`, медиафайл из `media/`.
- **Анти-бан:** большие случайные паузы (30–90 сек между сообщениями, перерыв 5–15 мин каждые 50 чатов), опциональное окно `active_hours`, авто-wait по `FloodWait`.
- **Прогресс:** `progress.json` (отправлено/ошибка/причина), resume при рестарте, итоговый отчёт.

## Стек
Python 3.11+, Telethon, python-dotenv. Качество: ruff (lint), mypy (опц.), pytest. Зависимости в `requirements.txt`.

## Структура проекта
```
reklama/
  .env.example          # API_ID, API_HASH, SESSION_NAME
  .gitignore            # .env, *.session, progress.json, logs/, media/*
  requirements.txt      # telethon, python-dotenv
  config.py             # все настройки + дефолты + переопределение из .env
  auth.py               # создание/загрузка сессии Telethon, интерактивный логин
  dialogs.py            # перебор диалогов, фильтрация по типу и правам (чистые функции)
  sender.py             # сборка сообщения, отправка медиа, классификация ошибок
  progress.py           # чтение/запись progress.json, resume (чистые функции)
  run.py                # CLI (argparse): оркестрация запуска
  message.txt           # текст рассылки
  media/                # сюда кладётся медиафайл
  logs/                 # логи запусков
```

## Детали реализации

### config.py
Все тюнябельные параметры с дефолтами, переопределяемые через `.env`:
- `MESSAGE_FILE` (по умолч. `message.txt`)
- `MEDIA_PATH` (по умолч. первый файл из `media/`)
- `FORCE_DOCUMENT` (bool) — всегда отправлять как документ
- `DIALOG_TYPES` — фиксированно `groups` (только группы/супергруппы)
- `DELAY_MIN_SEC`, `DELAY_MAX_SEC` (по умолч. 30, 90)
- `BATCH_SIZE` (50), `BATCH_PAUSE_MIN_SEC`, `BATCH_PAUSE_MAX_SEC` (300, 900)
- `ACTIVE_HOURS` (например `"09:00-21:00"`, опц.; вне окна — ожидание или пропуск по флагу)
- `DRY_RUN`, `LIMIT`, `RESET_PROGRESS` (из CLI)

### auth.py
- Загрузка `API_ID`, `API_HASH`, `SESSION_NAME` из `.env`.
- `TelegramClient(session, api_id, api_hash)`.
- При первом запуске — интерактивный ввод телефона + кода (+ 2FA пароль, если есть). Файл сессии `.session` сохраняется; в `.gitignore`.

### dialogs.py
Чистые функции (тестируемые без сети):
- `is_group(entity)` — `True` для `Chat`, `Channel` с `megagroup=True`. Исключает каналы-трансляции (`Channel.broadcast=True`) и удалённые.
- `filter_dialogs(dialogs)` — оставляет только группы; для каналов проверяет админ-права текущего аккаунта (через `client.get_entity` + проверку прав при необходимости).
- Перебор: `async for dialog in client.iter_dialogs()`.

### sender.py
- `detect_media_kind(path)` — по расширению/mimetype: image → `photo`, video → `video`, иначе `document`. `FORCE_DOCUMENT` перекрывает.
- `send(client, entity, text, media_path)`:
  - Если медиа есть — `client.send_file(entity, media_path, caption=text, force_document=...)` (вид/фото/видео по kind).
  - Иначе — `client.send_message(entity, text)`.
- Классификация ошибок → статус и причина для `progress.py`:
  - `ChatWriteForbiddenError`, `UserBannedInChannelError`, `ChannelPrivateError` → `skipped` (нет прав).
  - `SlowModeWaitError` → `skipped` (slow-mode; опц. можно дождаться, но по умолчанию пропуск+лог).
  - `FloodWaitError` → ждать требуемое время (Telethon), затем `sent`/`retry`; если слишком долго — лог и продолжить.
  - Прочее → `error` + repr.

### progress.py
Чистые функции над `progress.json`:
- `load()` → dict `{chat_id: {status, reason, ts}}`.
- `mark_sent(chat_id)`, `mark_skipped(chat_id, reason)`, `mark_error(chat_id, reason)`.
- `should_skip(chat_id)` — для resume (уже `sent`).
- `report()` — сводка: отправлено / пропущено / ошибок / всего.

### run.py (CLI, argparse)
Флаги: `--dry-run`, `--limit N`, `--reset-progress`.
Поток:
1. Загрузить конфиг/`.env`.
2. Прочитать `message.txt`; проверить существование `MEDIA_PATH`.
3. `auth.get_client()` + `client.start()`.
4. `dialogs.filter_dialogs(...)` → список групп. При `--dry-run` вывести список + что будет пропущено (уже отправленные по `progress.json`), затем выйти.
5. Перебор с задержками (`DELAY_MIN..MAX`), batch-паузами каждые `BATCH_SIZE`, учётом `ACTIVE_HOURS`.
6. Для каждой группы: resume-проверка (`progress.should_skip`) → `sender.send` → запись статуса.
7. В конце — `progress.report()` + запись лога в `logs/<timestamp>.log`.

## Валидация
- `python run.py --dry-run` — корректность фильтрации (только группы, верный список, пропуски по причинам).
- `python run.py --limit 1` — дымовой тест: одно сообщение реально доходит до тест-группы, `progress.json` обновляется.
- Перезапуск после `--limit 1` без `--reset-progress` — подтверждение resume (ранее отправленная группа пропускается).
- `ruff check .` чисто.
- `pytest`: юнит-тесты на `dialogs.is_group`/`filter_dialogs` (мок-сущности) и `progress.*` (load/mark/resume/report).

## Риски и смягчения
- **Бан аккаунта** — только группы (без ЛС), большие случайные паузы, batch-перерывы, окно `active_hours`, готовность к `FloodWait`. Пользователь использует на свой риск.
- **FloodWait** — авто-ожидание требуемого времени; лог.
- **Slow-mode группы** — пропуск + лог причины.
- **Запрет медиа/ссылок в группе** — перехват ошибки, `skipped` + причина.
- **Кража `.session`** — в `.gitignore`, предупреждение в `.env.example`/README.

## Out of scope
- Резидентный бот с listener'ом команд (запуск по команде из контрольного чата).
- Расписание/cron (запуск ручной; при желании — через планировщик ОС).
- Рассылка в ЛС и каналы.
- Поддержка нескольких медиафайлов / галерей (один файл за запуск).
