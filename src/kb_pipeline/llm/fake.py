from __future__ import annotations

import json

from kb_pipeline.models import Hierarchy


class FakeBackend:
    """Deterministic backend for tests and --fake smoke runs. No network."""

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        lowered = system.lower()
        if "структуризатор" in lowered or "иерарх" in lowered:
            return self._structure(user)
        if "критик" in lowered:
            return self._critic()
        if "полировщик" in lowered:
            return self._polish(user)
        return self._generate(user)

    def _structure(self, user: str) -> str:
        payload = json.loads(user) if user.strip().startswith("{") else {}
        subchat = payload.get("subchat_id") or "topic"
        messages = payload.get("messages") or []
        refs = []
        for item in messages:
            text = (item.get("text") or "").strip()
            if not text:
                continue
            refs.append(
                {
                    "id": item.get("id"),
                    "context": text[:120].replace("\n", " "),
                }
            )
            if len(refs) >= 12:
                break
        if not refs:
            for item in messages[:5]:
                refs.append({"id": item.get("id"), "context": "сообщение без текста"})
        folder = payload.get("subchat_id") or "Общее"
        title = f"Обзор {subchat}"
        hierarchy = Hierarchy.model_validate(
            {
                "folders": {
                    folder: {
                        "articles": {
                            f"obzor-{subchat}": {
                                "title": title,
                                "source_subchat": subchat,
                                "messages": refs,
                            }
                        },
                        "folders": {},
                    }
                }
            }
        )
        return json.dumps(hierarchy.model_dump(), ensure_ascii=False)

    def _generate(self, user: str) -> str:
        payload = json.loads(user) if user.strip().startswith("{") else {}
        title = payload.get("title") or "Статья"
        messages = payload.get("messages") or []
        lines = [f"# {title}", "", "## Введение", ""]
        existing = payload.get("existing_draft") or ""
        if existing:
            lines.append("Продолжение черновика по новому файлу сообщений.")
            lines.append("")
        lines.append("## Основная часть")
        lines.append("")
        for item in messages:
            text = (item.get("text") or "").strip()
            if not text:
                continue
            date = item.get("date")
            author = item.get("author")
            mid = item.get("id")
            snippet = text.splitlines()[0][:200]
            lines.append(f"- {snippet} [сообщение от {date}, {author}](id:{mid})")
            for att in item.get("attachments") or []:
                path = att.get("path") or ""
                desc = att.get("description") or path
                if path.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                    lines.append(f"  - ![{desc}]({path})")
                elif path:
                    lines.append(f"  - [{desc}]({path})")
        lines.extend(["", "## Заключение", "", "Сводка по сообщениям текущего файла."])
        return "\n".join(lines) + "\n"

    def _critic(self) -> str:
        return json.dumps(
            {
                "remarks": [
                    {
                        "section": "В целом",
                        "kind": "note",
                        "text": "Статья не содержит явных противоречий с сообщениями текущего файла.",
                    }
                ],
                "questions": [
                    "Какие ограничения исходного обсуждения стоит явно зафиксировать?",
                    "Есть ли связанные статьи, на которые стоит сослаться?",
                ],
            },
            ensure_ascii=False,
        )

    def _polish(self, user: str) -> str:
        payload = json.loads(user) if user.strip().startswith("{") else {}
        draft = payload.get("draft") or "# Статья\n"
        questions = payload.get("critic", {}).get("questions") if isinstance(payload.get("critic"), dict) else []
        extra = ["", "## Открытые вопросы", ""]
        if questions:
            extra.extend(f"- {q}" for q in questions)
        else:
            extra.append("- Уточнить границы темы при следующем файле сообщений.")
        return draft.rstrip() + "\n" + "\n".join(extra) + "\n"
