from __future__ import annotations

import argparse
import json
import logging
import sys

from dotenv import load_dotenv

from kb_pipeline.config import load_config
from kb_pipeline.graph import PipelineRuntime, compiled_graph
from kb_pipeline.ingest import (
    SubchatNotFoundError,
    UsageError,
    build_work_items,
    list_subchats,
)
from kb_pipeline.persist import ensure_output_dirs, load_hierarchy
from kb_pipeline.state import STAGE_ORDER

logger = logging.getLogger(__name__)
LAST_RUN = "last_run.json"


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="kb-pipeline",
        description="Собрать базу знаний из Telegram SQLite-экспорта (один токен-батч за итерацию).",
    )
    parser.add_argument("--config", default="config.yaml", help="Путь к config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Запустить пайплайн")
    _add_run_flags(run_p)

    resume_p = sub.add_parser("resume", help="Продолжить последний прогон с чекпоинта")
    resume_p.add_argument("--fake", action="store_true")

    list_p = sub.add_parser("list-subchats", help="Показать доступные подчаты")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.command == "list-subchats":
        return cmd_list(args)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "resume":
        return cmd_resume(args)
    parser.error("unknown command")
    return 2


def _add_run_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--subchat",
        action="append",
        dest="subchats",
        help="Идентификатор подчата: topic_N, N или chat_{telegram_id} (можно повторять). Обязателен, если нет --all.",
    )
    parser.add_argument("--all", action="store_true", help="Обработать все подчаты выбранных чатов")
    parser.add_argument("--part", type=int, default=None, help="Только батч N внутри выбранных подчатов")
    parser.add_argument("--from-part", type=int, default=None, help="С батча N (включительно)")
    parser.add_argument("--to-part", type=int, default=None, help="По батч N (включительно)")
    parser.add_argument(
        "--until",
        choices=STAGE_ORDER,
        default="polish",
        help="Остановиться после этого этапа каждого батча",
    )
    parser.add_argument(
        "--fake",
        action="store_true",
        help="Детерминированные заглушки LLM (без сети)",
    )
    parser.add_argument("--thread-id", default=None)


def cmd_list(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    try:
        items = list_subchats(config)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not items:
        print("Подчаты не найдены в базе.", file=sys.stderr)
        return 1
    for item in items:
        thread = "" if item.thread_id is None else str(item.thread_id)
        chat_label = item.chat.username or str(item.chat.telegram_id)
        print(
            "\t".join(
                [
                    chat_label,
                    item.subchat_id,
                    thread,
                    item.title,
                    str(item.message_count),
                ]
            )
        )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    ensure_output_dirs(config)
    try:
        items = build_work_items(
            config,
            subchats=args.subchats,
            all_subchats=args.all,
            part=args.part,
            from_part=args.from_part,
            to_part=args.to_part,
        )
    except SubchatNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    except (FileNotFoundError, ValueError, UsageError) as exc:
        print(exc, file=sys.stderr)
        return 1

    hierarchy = load_hierarchy(config)
    initial = {
        "hierarchy": hierarchy.model_dump(),
        "articles": {},
        "work_items": [item.model_dump() for item in items],
        "work_index": 0,
        "current_messages": [],
        "touched_article_ids": [],
        "until": args.until,
        "done": False,
        "status": "started",
    }
    # Reload existing article artifacts so incremental generate can continue.
    for folder_path, slug, article in hierarchy.iter_articles():
        article_id = hierarchy.article_id(folder_path, slug)
        from kb_pipeline.persist import load_article_record

        record = load_article_record(
            config, article_id, article.title, folder_path, slug
        )
        initial["articles"][article_id] = record.model_dump()

    thread_id = args.thread_id or _thread_id(items, args.until)
    return _invoke(config, initial, thread_id, fake=args.fake, persist_last=True)


def cmd_resume(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    last_path = config.output_dir_resolved / LAST_RUN
    if not last_path.exists():
        print("Нет last_run.json — сначала выполните run.", file=sys.stderr)
        return 1
    meta = json.loads(last_path.read_text(encoding="utf-8"))
    thread_id = meta["thread_id"]
    fake = args.fake or meta.get("fake", False)
    checkpoint = config.output_dir_resolved / ".checkpoints" / "lg.sqlite"
    runtime = PipelineRuntime(config=config, fake=fake)
    graph_config = {
        "configurable": {
            "thread_id": thread_id,
            "pipeline_runtime": runtime,
        }
    }
    logger.info("Resume thread_id=%s", thread_id)
    with compiled_graph(checkpoint) as app:
        snapshot = app.get_state(graph_config)
        if snapshot is None or not getattr(snapshot, "values", None):
            print("Чекпоинт пуст. Запустите run заново.", file=sys.stderr)
            return 1
        values = snapshot.values
        if values.get("done"):
            print("Последний прогон уже завершён.")
            return 0
        next_nodes = getattr(snapshot, "next", ()) or ()
        if not next_nodes:
            print("Нет незавершённых узлов.")
            return 0
        result = app.invoke(None, graph_config)
    print(result.get("status", "ok"))
    return 0


def _invoke(config, initial: dict, thread_id: str, *, fake: bool, persist_last: bool) -> int:
    runtime = PipelineRuntime(config=config, fake=fake)
    checkpoint = config.output_dir_resolved / ".checkpoints" / "lg.sqlite"
    graph_config = {
        "configurable": {
            "thread_id": thread_id,
            "pipeline_runtime": runtime,
        }
    }
    if persist_last:
        (config.output_dir_resolved / LAST_RUN).write_text(
            json.dumps(
                {
                    "thread_id": thread_id,
                    "fake": fake,
                    "until": initial.get("until"),
                    "work_items": initial.get("work_items"),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    logger.info(
        "Run thread_id=%s files=%s until=%s fake=%s",
        thread_id,
        len(initial.get("work_items") or []),
        initial.get("until"),
        fake,
    )
    with compiled_graph(checkpoint) as app:
        result = app.invoke(initial, graph_config)
    print(result.get("status", "ok"))
    if result.get("done"):
        print(f"Иерархия: {config.output_dir_resolved / 'hierarchy.json'}")
    return 0


def _thread_id(items, until: str) -> str:
    import hashlib

    blob = json.dumps(
        {"items": [item.model_dump() for item in items], "until": until},
        ensure_ascii=False,
        sort_keys=True,
    )
    return "kb-" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


if __name__ == "__main__":
    raise SystemExit(main())
