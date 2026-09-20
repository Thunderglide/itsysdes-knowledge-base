from __future__ import annotations

import json

from kb_pipeline.models import Hierarchy


class FakeBackend:
    """Deterministic backend for tests and --fake smoke runs. No network."""

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        lowered = system.lower()
        if "слияни" in lowered:
            return self._merge(user)
        if "разбиени" in lowered:
            return self._split(user)
        if "таксоном" in lowered or "раскладк" in lowered:
            return self._reparent(user)
        if "разметки тег" in lowered or "тегами статей" in lowered:
            return self._tag(user)
        if "склейк" in lowered or "консервативн" in lowered:
            return self._curate_polish(user)
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

    def _merge(self, user: str) -> str:
        payload = json.loads(user) if user.strip().startswith("{") else {}
        articles = payload.get("articles") or []
        stubs = [
            item
            for item in articles
            if int(item.get("message_count") or 0) <= 5
        ]
        targets = [
            item
            for item in articles
            if int(item.get("message_count") or 0) > 5
        ]
        actions = []
        if stubs and targets:
            stub = stubs[0]
            folder = stub.get("folder")
            same = [item for item in targets if item.get("folder") == folder] or targets
            actions.append(
                {
                    "sources": [stub.get("id")],
                    "target": same[0].get("id"),
                    "reason": "fake merge",
                }
            )
        return json.dumps({"actions": actions}, ensure_ascii=False)

    def _split(self, user: str) -> str:
        payload = json.loads(user) if user.strip().startswith("{") else {}
        actions = []
        for candidate in payload.get("candidates") or []:
            sections = [
                item.get("heading")
                for item in (candidate.get("sections") or [])
                if item.get("heading")
            ]
            if len(sections) < 2:
                continue
            mid = max(1, len(sections) // 2)
            folder = candidate.get("folder") or ""
            actions.append(
                {
                    "source": candidate.get("id"),
                    "parts": [
                        {
                            "title": f"{candidate.get('title')} (1)",
                            "slug": "",
                            "folder": folder,
                            "headings": sections[:mid],
                        },
                        {
                            "title": f"{candidate.get('title')} (2)",
                            "slug": "",
                            "folder": folder,
                            "headings": sections[mid:],
                        },
                    ],
                }
            )
            break
        return json.dumps({"actions": actions}, ensure_ascii=False)

    def _reparent(self, user: str) -> str:
        payload = json.loads(user) if user.strip().startswith("{") else {}
        folders = payload.get("taxonomy", {}).get("folders") or []
        sql_folder = next((item for item in folders if item.endswith("/SQL") or item.endswith("SQL")), "")
        actions = []
        for article in payload.get("articles") or []:
            title = str(article.get("title") or "").lower()
            current = str(article.get("folder") or "")
            if "sql" in title and sql_folder and sql_folder != current:
                actions.append(
                    {
                        "id": article.get("id"),
                        "folder": sql_folder,
                        "reason": "fake sql reparent",
                    }
                )
        return json.dumps({"actions": actions, "proposed_folders": []}, ensure_ascii=False)

    def _tag(self, user: str) -> str:
        payload = json.loads(user) if user.strip().startswith("{") else {}
        allowed = set(payload.get("allowed", {}).get("topics") or [])
        allowed.update(payload.get("allowed", {}).get("tech") or [])
        allowed.update(payload.get("allowed", {}).get("formats") or [])
        actions = []
        for article in payload.get("articles") or []:
            title = str(article.get("title") or "").lower()
            tags: list[str] = []
            for token in ("sql", "kafka", "bpmn", "rest", "ux", "требования"):
                if token in title and token in allowed:
                    tags.append(token)
            if "howto" in allowed:
                tags.append("howto")
            actions.append({"id": article.get("id"), "tags": tags, "proposed_tags": []})
        return json.dumps({"actions": actions}, ensure_ascii=False)

    def _curate_polish(self, user: str) -> str:
        payload = json.loads(user) if user.strip().startswith("{") else {}
        text = str(payload.get("text") or "")
        return text if text.endswith("\n") or not text else text + "\n"
