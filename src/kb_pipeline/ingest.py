from __future__ import annotations

from pathlib import Path

from kb_pipeline.config import Config
from kb_pipeline.models import Message, WorkItem
from kb_pipeline.parser import messages_to_jsonl
from kb_pipeline.source import (
    SubchatInfo,
    connect,
    list_chats,
    list_subchats as load_subchats,
    load_thread_messages,
    load_work_item_messages,
    pack_batches,
    parse_telegram_message_id,
    subchat_matches,
)


class SubchatNotFoundError(LookupError):
    pass


class UsageError(ValueError):
    pass


def list_subchats(config: Config) -> list[SubchatInfo]:
    with connect(config) as conn:
        chats = list_chats(conn, config.chats)
        return load_subchats(conn, chats)


def resolve_subchats(
    config: Config, requested: list[str] | None, all_subchats: bool
) -> list[SubchatInfo]:
    available = list_subchats(config)
    if not available:
        raise UsageError("В базе нет сообщений для выбранных чатов.")
    if all_subchats:
        return available
    if not requested:
        names = [item.subchat_id for item in available]
        raise UsageError(
            "Укажите --subchat TOPIC или --all.\n"
            "Доступные подчаты:\n  " + "\n  ".join(names)
        )
    resolved: list[SubchatInfo] = []
    for token in requested:
        match = _match_subchat(available, token)
        if match is None:
            names = [item.subchat_id for item in available]
            raise SubchatNotFoundError(
                f"Подчат {token!r} не найден. Доступные: {', '.join(names)}"
            )
        resolved.append(match)
    return resolved


def _match_subchat(available: list[SubchatInfo], token: str) -> SubchatInfo | None:
    for item in available:
        if subchat_matches(item, token):
            return item
    return None


def filter_part_indices(
    indices: list[int],
    *,
    part: int | None,
    from_part: int | None,
    to_part: int | None,
) -> list[int]:
    if part is not None and (from_part is not None or to_part is not None):
        raise ValueError("Нельзя сочетать --part с --from-part/--to-part")
    selected: list[int] = []
    for index in indices:
        if part is not None and index != part:
            continue
        if from_part is not None and index < from_part:
            continue
        if to_part is not None and index > to_part:
            continue
        selected.append(index)
    return selected


def build_work_items(
    config: Config,
    *,
    subchats: list[str] | None,
    all_subchats: bool,
    part: int | None,
    from_part: int | None,
    to_part: int | None,
) -> list[WorkItem]:
    selected = resolve_subchats(config, subchats, all_subchats)
    items: list[WorkItem] = []
    with connect(config) as conn:
        for info in selected:
            messages = load_thread_messages(
                conn,
                chat=info.chat,
                thread_id=info.thread_id,
                subchat_id=info.subchat_id,
            )
            batches = pack_batches(
                messages,
                max_tokens=config.max_batch_tokens,
                max_chars=config.max_message_chars,
            )
            if not batches:
                if all_subchats:
                    continue
                raise FileNotFoundError(
                    f"Нет сообщений для {info.subchat_id} с заданным фильтром"
                )
            indices = list(range(1, len(batches) + 1))
            kept = filter_part_indices(
                indices, part=part, from_part=from_part, to_part=to_part
            )
            if not kept:
                if all_subchats:
                    continue
                raise FileNotFoundError(
                    f"Нет батчей для {info.subchat_id} с заданным фильтром"
                )
            folder_name = "/".join(
                part_name
                for part_name in (info.chat.username or str(info.chat.telegram_id), info.subchat_id)
                if part_name
            )
            for index in kept:
                batch = batches[index - 1]
                first_id = parse_telegram_message_id(batch[0].id)
                last_id = parse_telegram_message_id(batch[-1].id)
                part_file = f"batch_{index:03d}"
                for message in batch:
                    message.part_file = part_file
                items.append(
                    WorkItem(
                        subchat_id=info.subchat_id,
                        folder_name=folder_name,
                        part_file=part_file,
                        part_index=index,
                        chat_db_id=info.chat.id,
                        telegram_id=info.chat.telegram_id,
                        thread_id=info.thread_id,
                        first_message_id=first_id,
                        last_message_id=last_id,
                    )
                )
    if not items:
        raise FileNotFoundError("Нет сообщений для выбранных подчатов")
    return items


def ingest_part(config: Config, item: WorkItem) -> tuple[list[Message], Path]:
    with connect(config) as conn:
        messages = load_work_item_messages(conn, item)
    cache_dir = config.output_dir_resolved / "cache" / item.subchat_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{item.part_file}.jsonl"
    cache_path.write_text(messages_to_jsonl(messages), encoding="utf-8")
    return messages, cache_path
