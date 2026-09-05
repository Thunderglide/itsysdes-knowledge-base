# kb-pipeline

Мультиагентный пайплайн на LangGraph: берёт SQLite-экспорт Telegram и собирает из него Markdown-базу знаний.

Нужен Python **3.11+**.

## Скачать проект

Через Git:

```bash
git clone https://github.com/Thunderglide/itsysdes-knowledge-base.git
cd itsysdes-knowledge-base
```

Без Git — ZIP с GitHub: **Code → Download ZIP**, распаковать и перейти в папку проекта.

## Какие файлы нужно добавить после клонирования

В репозитории уже есть шаблоны. Локальные секреты и пути **не** хранятся в Git — их нужно создать у себя.

### 1. `.env` (секреты)

Скопируйте шаблон и подставьте ключ:

```bash
cp .env.example .env
```

На Windows (cmd):

```bat
copy .env.example .env
```

Минимальное содержимое `.env`:

```env
# Cursor user API key (Dashboard → Integrations). Нужен, если backend агента = cursor.
CURSOR_API_KEY=cursor_...

# Необязательно: переопределить модель LM Studio (иначе берётся из config.yaml или /v1/models).
# LMSTUDIO_MODEL=qwen2.5-32b-instruct
```

Файл `.env` должен лежать в **корне проекта** (рядом с `pyproject.toml`). Его нельзя коммитить.

### 2. `config.yaml` (локальные пути)

```bash
cp config.example.yaml config.yaml
```

На Windows (cmd):

```bat
copy config.example.yaml config.yaml
```

В `config.yaml` укажите свои пути к экспорту Telegram:

```yaml
sqlite_path: /absolute/path/to/telegram_export.db
files_root: /absolute/path/to/tg-loader/data
chats: []          # пусто = все чаты; иначе username / telegram_id / title
output_dir: kb
```

`sqlite_path` — файл базы экспорта. `files_root` — каталог, относительно которого лежат вложения (`files/{chat}/{msg}/...`).

### 3. `.gitignore`

Файл `.gitignore` уже есть в репозитории. Дополнительно ничего создавать не нужно.

Он исключает из Git:

| Путь | Зачем |
| --- | --- |
| `.env`, `.env.*` (кроме `.env.example`) | секреты |
| `config.yaml` | локальные пути к вашей базе Telegram |
| `.venv/`, `venv/` | виртуальное окружение |
| `kb/*` (кроме `kb/.gitkeep`) | результаты пайплайна: статьи, кэш, чекпоинты, `hierarchy.json`, `last_run.json` |
| `.cursor-scratch/` | рабочие файлы Cursor-агентов |
| `*.db`, `*.sqlite` | локальные базы |
| `__pycache__/`, `.pytest_cache/` | артефакты Python |

Если нужно версионировать готовую базу знаний, уберите `kb/*` из `.gitignore` **осознанно**: в `kb/articles/` и `kb/hierarchy.json` могут быть тексты из исходных чатов.

## Виртуальное окружение

Команды выполнять из корня проекта.

### macOS и Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Проверка, что окружение активно: в начале приглашения оболочки появится `(.venv)`.

Деактивировать:

```bash
deactivate
```

### Windows (cmd)

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

### Windows (PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Если PowerShell запрещает скрипты:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

После установки в PATH появится команда `kb-pipeline`.

## Как запускать

Перед запуском:

1. Активируйте `.venv`.
2. Заполните `.env` и `config.yaml`.
3. Если агенты с `backend: cursor` — нужен ключ `CURSOR_API_KEY`.
4. Если агенты с `backend: lmstudio` — должен быть запущен LM Studio на `base_url` из конфига.

Посмотреть подчаты (форум-топики) в базе:

```bash
kb-pipeline list-subchats
```

Обработать один подчат:

```bash
kb-pipeline run --subchat topic_123
```

Обработать все подчаты выбранных чатов:

```bash
kb-pipeline run --all
```

Только конкретный батч сообщений:

```bash
kb-pipeline run --subchat topic_123 --part 1
```

Остановиться после этапа (`structure` → `generate` → `critique` → `polish`):

```bash
kb-pipeline run --subchat topic_123 --until generate
```

Прогон без сети, на заглушках LLM (удобно для проверки установки):

```bash
kb-pipeline run --subchat topic_123 --fake
```

Продолжить прерванный прогон с чекпоинта:

```bash
kb-pipeline resume
```

Другой файл конфигурации:

```bash
kb-pipeline --config /path/to/config.yaml list-subchats
```

Результаты пишутся в `output_dir` (по умолчанию `kb/`):

```
kb/
  articles/<раздел>/<slug>.draft.md
  articles/<раздел>/<slug>.critic.json
  articles/<раздел>/<slug>.md
  cache/
  .checkpoints/lg.sqlite
  hierarchy.json
  last_run.json
```

### Тесты

```bash
pytest
```

## Структура репозитория

```
.
├── .env.example          # шаблон секретов → копировать в .env
├── .gitignore
├── config.example.yaml   # шаблон конфига → копировать в config.yaml
├── pyproject.toml
├── prompts/              # промпты агентов (можно править)
├── src/kb_pipeline/      # код пайплайна
└── tests/
```
