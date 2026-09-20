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
# DeepSeek API key (https://platform.deepseek.com/api_keys). Нужен, если backend агента = deepseek.
DEEPSEEK_API_KEY=sk-...

# Необязательно: переопределить модель DeepSeek (иначе берётся из config.yaml).
# DEEPSEEK_MODEL=deepseek-flash

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
| `graphify-out/cost.json`, `graphify-out/.graphify_python`, `graphify-out/.graphify_root`, `graphify-out/cache/`, датированные бэкапы | локальные служебные файлы Graphify |
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
3. Если агенты с `backend: deepseek` — нужен ключ `DEEPSEEK_API_KEY`.
4. Если агенты с `backend: lmstudio` — должен быть запущен LM Studio на `base_url` из конфига, **и модель должна быть загружена** (см. [LM Studio и LM Link](#lm-studio-и-lm-link)).

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

`resume` продолжает тот же чекпоинт (`kb/.checkpoints/lg.sqlite`, `kb/last_run.json`). Смена промптов structure подхватывается на следующем батче, граф и стейт менять не нужно.

После того как прогон дошёл до конца (`done`), статьи можно подчистить командами `curate`. Каждая команда пишет JSON-план (`--dry-run`) и применяет его (`--apply`). **Не запускайте `--apply`, пока `resume` не завершился** — иначе чекпоинт перезапишет `hierarchy.json`.

Рекомендуемый порядок:

1. `cleanup` — удалить пустые оболочки и слить кросс-папочные дубликаты заголовков.
2. `merge` — слить мелкие заглушки в более крупные статьи.
3. `split` — разрезать слишком большие статьи.
4. `reparent` — разложить статьи по папкам из [`taxonomy.yaml`](taxonomy.yaml).
5. `relink` — починить межстраничные ссылки после переносов и слияний (без LLM).
6. `tag` — проставить теги из закрытого словаря в `taxonomy.yaml`.

```bash
kb-pipeline curate cleanup --dry-run
kb-pipeline curate cleanup --apply kb/curate/cleanup-plan.json

kb-pipeline curate merge --dry-run
# правки kb/curate/merge-plan.json
kb-pipeline curate merge --apply kb/curate/merge-plan.json

kb-pipeline curate split --dry-run
kb-pipeline curate split --apply kb/curate/split-plan.json

kb-pipeline curate reparent --dry-run
kb-pipeline curate reparent --apply kb/curate/reparent-plan.json

kb-pipeline curate relink --dry-run
kb-pipeline curate relink --apply kb/curate/relink-plan.json

kb-pipeline curate tag --dry-run
kb-pipeline curate tag --apply kb/curate/tag-plan.json
```

`relink` детерминированный: живой slug → текущий `folder/slug.md`, удалённый slug → цель из `merge-plan.json` / `cleanup-plan.json` / `kb/curate/link-aliases.json`. Вложения `files/`, цитаты `id:` и `http(s)` не трогает. Неразрешённые ссылки попадают в поле `unresolved` плана. После `merge` и `reparent` ссылки пересчитываются автоматически.

`--fake` на curate использует заглушки LLM (для `relink` и `cleanup` LLM не нужен). `--apply` делает backup в `kb/curate/backup-<время>/`. `--polish` / `--no-polish` — консервативно пригладить markdown после apply (по умолчанию включено для `merge`/`split`).

Таксономия папок и тегов задаётся в [`taxonomy.yaml`](taxonomy.yaml). Пороги куратора — в `config.yaml` (секция `curate`, см. `config.example.yaml`).

Очистить незавершённую сессию (чекпоинт, `last_run.json`, кэш ingest текущего прогона, `*.draft.md` и `*.critic.json` затронутых статей). Финальные `*.md` и `hierarchy.json` не трогает:

```bash
kb-pipeline clean --session
kb-pipeline clean --session -y   # без подтверждения
```

Если последний прогон уже дошёл до конца (`done` в чекпоинте), `--session` ничего не удаляет.

Удалить только разросшийся LangGraph-чекпоинт `kb/.checkpoints/lg.sqlite` (статьи, `hierarchy.json` и `last_run.json` остаются). После этого `resume` уже не продолжит старый прогон:

```bash
kb-pipeline clean --checkpoints
kb-pipeline clean --checkpoints -y
```

Снести всю собранную базу знаний в `output_dir` (кроме `kb/.gitkeep`):

```bash
kb-pipeline clean --kb
kb-pipeline clean --kb -y
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

## DeepSeek

Ключ берётся из переменной `DEEPSEEK_API_KEY` (см. `.env.example`). В `config.yaml` укажите:

```yaml
deepseek:
  base_url: https://api.deepseek.com
  api_key_env: DEEPSEEK_API_KEY
  model: deepseek-flash
  timeout_sec: 600
  max_retries: 0

agents:
  structure:
    backend: deepseek
    model: deepseek-flash
```

Модель по умолчанию — `deepseek-flash`. Альтернатива: `deepseek-v4-pro`. Ключ: [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys).

## LM Studio и LM Link

Сервер на `base_url` (по умолчанию `http://127.0.0.1:1234/v1`) должен работать **и** иметь загруженную модель. Скачанные веса или модель, видимая через LM Link на другом компьютере, сами по себе не обслуживают `POST /v1/chat/completions`.

Типичная ошибка: `No models loaded. Please load a model in the developer page or use the 'lms load' command.`

Порядок проверки:

1. В LM Studio сервер в состоянии Running (вкладка Developer).
2. Для LM Link: `lms link status` — удалённая машина online; при нескольких устройствах задайте preferred device (`lms link set-preferred-device`).
3. Загрузите модель: Developer → Load или `lms load qwen/qwen3.5-9b` (id должен совпадать с `lmstudio.model` / `agents.*.model` в `config.yaml`).
4. `lms ps` не пустой.
5. `curl http://127.0.0.1:1234/v1/models` возвращает тот же id.
6. Либо включите Just-in-Time loading в настройках сервера, чтобы модель подгружалась по запросу.

Пайплайн сам `lms load` не вызывает. Id модели должен совпадать буквально: опечатка вроде `qqwen/...` вместо `qwen/...` даст ошибку со списком доступных id.

Запросы к `http://127.0.0.1:1234/v1` с LM Link остаются локальными: LM Studio сам маршрутизирует inference на удалённое устройство.

Локальная генерация на большом батче (десятки тысяч токенов) часто длится дольше 10 минут. Клиент OpenAI по умолчанию обрывает запрос через 600 с и **повторяет его ещё дважды** — второй вызов прилетает, пока первый ещё считается, и LM Studio отвечает ошибкой. В пайплайне это отключено: `lmstudio.max_retries: 0` и ожидание `lmstudio.timeout_sec: 3600` (0 = без лимита). LangGraph узлы сам по себе не ретраит.

## Graphify (карта репозитория для Cursor)

Graphify строит граф кода, чтобы агент Cursor сначала делал `graphify query` / `path` / `explain`, а не обходил все файлы grep’ом.

CLI **не** ставится в `.venv` проекта (официальный пакет — `graphifyy`):

```bash
uv tool install graphifyy
# или: pipx install graphifyy
```

В корне репозитория:

```bash
graphify cursor install
graphify extract . --code-only
graphify cluster-only . --no-label
```

`graphify cursor install` пишет [`.cursor/rules/graphify.mdc`](.cursor/rules/graphify.mdc) (`alwaysApply: true`). Отдельный скилл в `.cursor/skills/` не нужен — это дублировало бы правило.

Карта лежит в `graphify-out/` (`graph.json`, `GRAPH_REPORT.md`, `graph.html`). После правок кода:

```bash
graphify update .
```

Открыть визуализацию: `graphify-out/graph.html`.

## Структура репозитория

```
.
├── .cursor/rules/        # правило Graphify для Cursor
├── .env.example          # шаблон секретов → копировать в .env
├── .gitignore
├── CHANGELOG.md
├── config.example.yaml   # шаблон конфига → копировать в config.yaml
├── taxonomy.yaml         # папки и закрытый словарь тегов для curate
├── graphify-out/         # карта репозитория (Graphify)
├── pyproject.toml
├── prompts/              # промпты агентов (можно править)
├── src/kb_pipeline/      # код пайплайна
└── tests/
```
