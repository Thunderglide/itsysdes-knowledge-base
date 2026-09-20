from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import yaml

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_CITE_RE = re.compile(r"id:([0-9]+:[0-9]+:[0-9]+)")
_SCAFFOLD_HEADINGS = {
    "что нужно добавить",
    "возможные подразделы",
    "открытые вопросы",
}
_SCAFFOLD_QUOTE_RE = re.compile(
    r"^>\s*В текущем файле есть только одно сообщение",
    re.IGNORECASE,
)


@dataclass
class MarkdownSection:
    heading: str
    level: int
    body: str
    message_ids: list[str] = field(default_factory=list)

    def as_parser_dict(self) -> dict[str, Any]:
        return {
            "heading": self.heading,
            "level": self.level,
            "message_ids": list(self.message_ids),
        }


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        return {}, text or ""
    raw = yaml.safe_load(match.group(1)) or {}
    meta = raw if isinstance(raw, dict) else {}
    body = (text or "")[match.end() :]
    return meta, body


def strip_frontmatter(text: str) -> str:
    _meta, body = split_frontmatter(text)
    return body


def with_frontmatter(
    body: str,
    *,
    title: str,
    folder: str,
    tags: Iterable[str] | None = None,
) -> str:
    _meta, stripped = split_frontmatter(body or "")
    payload = {
        "title": title,
        "folder": folder,
        "tags": [str(tag) for tag in (tags or [])],
    }
    dumped = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False).rstrip()
    content = stripped.lstrip("\n")
    if content and not content.endswith("\n"):
        content += "\n"
    return f"---\n{dumped}\n---\n\n{content}"


def parse_sections(text: str) -> list[MarkdownSection]:
    body = strip_frontmatter(text)
    sections: list[MarkdownSection] = [
        MarkdownSection(heading="", level=0, body="", message_ids=[])
    ]
    current_lines: list[str] = []
    seen: set[str] = set()

    def flush() -> None:
        sections[-1].body = "\n".join(current_lines).strip("\n")
        if sections[-1].body:
            sections[-1].body += "\n"

    for line in body.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match and len(match.group(1)) >= 2:
            flush()
            heading = match.group(2).strip()
            sections.append(
                MarkdownSection(
                    heading=heading,
                    level=len(match.group(1)),
                    body="",
                    message_ids=[],
                )
            )
            current_lines = [line.rstrip()]
            seen = set()
            continue
        current_lines.append(line.rstrip())
        for cite in _CITE_RE.findall(line):
            if cite not in seen:
                sections[-1].message_ids.append(cite)
                seen.add(cite)
    flush()
    if (
        len(sections) > 1
        and not sections[0].heading
        and not sections[0].message_ids
        and not sections[0].body.strip()
    ):
        return sections[1:]
    return sections


def citation_ids(text: str) -> set[str]:
    return set(_CITE_RE.findall(text or ""))


def citations_lost(original: str, updated: str) -> bool:
    return not citation_ids(original).issubset(citation_ids(updated))


def strip_scaffold(text: str) -> str:
    sections = parse_sections(text)
    kept: list[str] = []
    for section in sections:
        if _norm_heading(section.heading) in _SCAFFOLD_HEADINGS:
            continue
        body = section.body
        if not section.heading:
            filtered = [
                line
                for line in body.splitlines()
                if not _SCAFFOLD_QUOTE_RE.match(line.strip())
            ]
            body = "\n".join(filtered).strip("\n")
            if body:
                body += "\n"
        if body.strip():
            kept.append(body.rstrip())
    return "\n\n".join(kept).strip() + ("\n" if kept else "")


def stitch_markdown(
    title: str,
    parts: list[tuple[str, str]],
) -> str:
    chunks: list[str] = [f"# {title}".rstrip()]
    for part_title, raw in parts:
        cleaned = strip_scaffold(raw)
        cleaned = _drop_h1(cleaned)
        if not cleaned.strip():
            continue
        heading = (part_title or "").strip()
        if heading and not _starts_with_heading(cleaned):
            chunks.append(f"## {heading}\n\n{cleaned.strip()}")
        else:
            chunks.append(cleaned.strip())
    body = "\n\n".join(chunk for chunk in chunks if chunk.strip()).strip()
    return body + ("\n" if body else "")


def extract_part_markdown(
    text: str,
    *,
    title: str,
    headings: Iterable[str] | None = None,
    message_ids: Iterable[str] | None = None,
) -> str:
    wanted_headings = {_norm_heading(item) for item in (headings or []) if str(item).strip()}
    wanted_ids = {str(item) for item in (message_ids or []) if str(item).strip()}
    picked: list[MarkdownSection] = []
    for section in parse_sections(text):
        if section.level < 2:
            continue
        heading_hit = bool(wanted_headings) and _norm_heading(section.heading) in wanted_headings
        id_hit = bool(wanted_ids) and bool(set(section.message_ids) & wanted_ids)
        if wanted_headings and heading_hit:
            picked.append(section)
        elif not wanted_headings and id_hit:
            picked.append(section)
    if not picked:
        return f"# {title}\n"
    chunks = [f"# {title}".rstrip()]
    for section in picked:
        body = section.body.strip()
        if body:
            chunks.append(body)
    return "\n\n".join(chunks).strip() + "\n"


def remainder_markdown(
    text: str,
    *,
    headings: Iterable[str] | None = None,
    message_ids: Iterable[str] | None = None,
) -> str:
    body = strip_frontmatter(text)
    wanted_headings = {_norm_heading(item) for item in (headings or []) if str(item).strip()}
    wanted_ids = {str(item) for item in (message_ids or []) if str(item).strip()}
    kept: list[str] = []
    for section in parse_sections(body):
        heading_hit = bool(wanted_headings) and _norm_heading(section.heading) in wanted_headings
        id_hit = bool(wanted_ids) and bool(set(section.message_ids) & wanted_ids)
        drop = False
        if section.level >= 2:
            if wanted_headings and heading_hit:
                drop = True
            elif not wanted_headings and id_hit:
                drop = True
        if drop:
            continue
        if section.body.strip():
            kept.append(section.body.strip())
    result = "\n\n".join(kept).strip()
    return result + ("\n" if result else "")


def first_heading(text: str) -> str:
    for line in strip_frontmatter(text).splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _drop_h1(text: str) -> str:
    lines = (text or "").splitlines()
    if lines and re.match(r"^#\s+[^#]", lines[0].strip()):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip()


def _starts_with_heading(text: str) -> bool:
    for line in (text or "").splitlines():
        if line.strip():
            return bool(_HEADING_RE.match(line.strip()))
    return False


def _norm_heading(value: str) -> str:
    return " ".join(value.strip().lower().split())
