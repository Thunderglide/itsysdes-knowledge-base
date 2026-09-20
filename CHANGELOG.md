# Changelog

## 0.2.0 — 2026-09-20

Гибридная таксономия, безопасный куратор и починка ссылок после переносов. Итоговые статьи по-прежнему живут в локальном `kb/` и в Git не входят.

### Пайплайн и LLM

- Backend DeepSeek (`deepseek-flash`) наряду с LM Studio; Cursor LLM backend убран.
- Агенты structure / generator / critic / polisher работают через `config.yaml` `agents.*`.
- Graphify-карта репозитория для агента Cursor (`.cursor/rules/graphify.mdc`, `graphify-out/`).

### Куратор (`kb-pipeline curate`)

Порядок после завершённого `run`: **cleanup → merge → split → reparent → relink → tag**.

| Команда | Что делает |
| --- | --- |
| `cleanup` | Удаляет пустые оболочки и сливает кросс-папочные дубли заголовков |
| `merge` | Сливает мелкие заглушки; сшивает markdown без полной регенерации |
| `split` | Режет крупные статьи по заголовкам и цитатам |
| `reparent` | Раскладывает статьи по папкам `taxonomy.yaml` |
| `relink` | Чинит межстраничные ссылки по slug и merge-алиасам (без LLM) |
| `tag` | Ставит теги из закрытого словаря |

Все команды — `--dry-run` / `--apply`. Apply пишет backup в `kb/curate/backup-<время>/`. После merge и reparent ссылки пересчитываются сами; merge сохраняет `kb/curate/link-aliases.json`.

### Очистка

- `clean --session` — незавершённый прогон (чекпоинт, кэш, черновики).
- `clean --checkpoints` — только `lg.sqlite` (статьи не трогает). Нужен, когда чекпоинт LangGraph разрастается на десятки гигабайт.
- `clean --kb` — снести всю базу в `output_dir`.

### Прочее

- Таксономия: [`taxonomy.yaml`](taxonomy.yaml).
- Промпты куратора: merger, splitter, reparent, tagger, curate_polish.
- `compact_message_contexts` уменьшает объём стейта в чекпоинте новых прогонов.
