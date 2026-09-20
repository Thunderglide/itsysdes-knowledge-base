from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from kb_pipeline.agents.critic import run_critic
from kb_pipeline.agents.generator import run_generator
from kb_pipeline.agents.polisher import run_polisher
from kb_pipeline.agents.structure import run_structure
from kb_pipeline.config import Config
from kb_pipeline.ingest import ingest_part
from kb_pipeline.llm.factory import get_backend
from kb_pipeline.models import (
    ArticleRecord,
    CriticReport,
    Hierarchy,
    Message,
    WorkItem,
    compact_message_contexts,
)
from kb_pipeline.persist import (
    article_final_is_current,
    load_article_record,
    save_article_artifacts,
    save_hierarchy,
)
from kb_pipeline.state import STAGE_ORDER, PipelineState

logger = logging.getLogger(__name__)


@dataclass
class PipelineRuntime:
    config: Config
    fake: bool = False
    backends: dict[str, Any] = field(default_factory=dict)

    def backend(self, name: str):
        if name not in self.backends:
            self.backends[name] = get_backend(name, self.config, fake=self.fake)
        return self.backends[name]


def _runtime(config: RunnableConfig) -> PipelineRuntime:
    runtime = (config or {}).get("configurable", {}).get("pipeline_runtime")
    if runtime is None:
        raise RuntimeError("pipeline_runtime is missing from graph config")
    return runtime


def ingest_node(state: PipelineState, config: RunnableConfig) -> dict[str, Any]:
    runtime = _runtime(config)
    items = [WorkItem.model_validate(item) for item in state.get("work_items") or []]
    index = state.get("work_index") or 0
    if index >= len(items):
        return {"done": True, "status": "completed", "current_messages": []}
    item = items[index]
    logger.info("Ingest %s / %s", item.subchat_id, item.part_file)
    messages, _cache = ingest_part(runtime.config, item)
    return {
        "done": False,
        "status": f"ingested {item.subchat_id}/{item.part_file}",
        "current_subchat_id": item.subchat_id,
        "current_part_file": item.part_file,
        "current_messages": [message.model_dump() for message in messages],
        "touched_article_ids": [],
    }


def structure_node(state: PipelineState, config: RunnableConfig) -> dict[str, Any]:
    runtime = _runtime(config)
    messages = [Message.model_validate(item) for item in state.get("current_messages") or []]
    hierarchy = Hierarchy.model_validate(state.get("hierarchy") or {"folders": {}})
    subchat_id = state.get("current_subchat_id") or ""
    part_file = state.get("current_part_file") or ""
    logger.info("Structure %s / %s (%s messages)", subchat_id, part_file, len(messages))
    updated, touched = run_structure(
        runtime.backend("structure"),
        runtime.config,
        hierarchy=hierarchy,
        messages=messages,
        subchat_id=subchat_id,
        part_file=part_file,
    )
    updated = compact_message_contexts(updated)
    save_hierarchy(runtime.config, updated)
    articles = dict(state.get("articles") or {})
    for article_id in touched:
        found = updated.find_by_id(article_id)
        if not found:
            continue
        folder, slug, article = found
        if article_id not in articles:
            record = load_article_record(
                runtime.config, article_id, article.title, folder, slug, tags=article.tags
            )
            articles[article_id] = record.model_dump()
        else:
            articles[article_id]["title"] = article.title
            articles[article_id]["folder"] = folder
            articles[article_id]["slug"] = slug
    return {
        "hierarchy": updated.model_dump(),
        "touched_article_ids": touched,
        "articles": articles,
        "status": f"structured {part_file}: {len(touched)} articles",
    }


def _article_messages(state: PipelineState, article_id: str) -> list[Message]:
    hierarchy = Hierarchy.model_validate(state.get("hierarchy") or {"folders": {}})
    found = hierarchy.find_by_id(article_id)
    current = [Message.model_validate(item) for item in state.get("current_messages") or []]
    if not found:
        return current
    allowed = found[2].message_ids()
    return [item for item in current if item.id in allowed]


def generate_node(state: PipelineState, config: RunnableConfig) -> dict[str, Any]:
    runtime = _runtime(config)
    articles = dict(state.get("articles") or {})
    backend = runtime.backend("generator")
    for article_id in state.get("touched_article_ids") or []:
        if article_id not in articles:
            continue
        record = ArticleRecord.model_validate(articles[article_id])
        messages = _article_messages(state, article_id)
        logger.info("Generate %s (%s msgs)", article_id, len(messages))
        record.draft = run_generator(
            backend,
            runtime.config,
            title=record.title,
            folder=record.folder,
            slug=record.slug,
            existing_draft=record.draft,
            messages=messages,
            subchat_id=state.get("current_subchat_id") or "",
        )
        save_article_artifacts(runtime.config, record)
        articles[article_id] = record.model_dump()
    return {"articles": articles, "status": "generated"}


def critic_node(state: PipelineState, config: RunnableConfig) -> dict[str, Any]:
    runtime = _runtime(config)
    articles = dict(state.get("articles") or {})
    backend = runtime.backend("critic")
    for article_id in state.get("touched_article_ids") or []:
        if article_id not in articles:
            continue
        record = ArticleRecord.model_validate(articles[article_id])
        messages = _article_messages(state, article_id)
        logger.info("Critic %s", article_id)
        report = run_critic(
            backend,
            runtime.config,
            draft=record.draft,
            messages=messages,
        )
        record.critic_comments = report.model_dump()
        save_article_artifacts(runtime.config, record)
        articles[article_id] = record.model_dump()
    return {"articles": articles, "status": "criticized"}


def polish_node(state: PipelineState, config: RunnableConfig) -> dict[str, Any]:
    runtime = _runtime(config)
    articles = dict(state.get("articles") or {})
    hierarchy = compact_message_contexts(
        Hierarchy.model_validate(state.get("hierarchy") or {"folders": {}})
    )
    save_hierarchy(runtime.config, hierarchy)
    listing = hierarchy.to_listing()
    backend = runtime.backend("polisher")
    for article_id in state.get("touched_article_ids") or []:
        if article_id not in articles:
            continue
        record = ArticleRecord.model_validate(articles[article_id])
        if article_final_is_current(runtime.config, record):
            loaded = load_article_record(
                runtime.config,
                article_id,
                record.title,
                record.folder,
                record.slug,
                tags=record.tags,
            )
            record.final = loaded.final
            articles[article_id] = record.model_dump()
            logger.info("Polish skip %s (final newer than draft and critic)", article_id)
            continue
        messages = _article_messages(state, article_id)
        critic = CriticReport.model_validate(record.critic_comments or {})
        logger.info("Polish %s", article_id)
        record.final = run_polisher(
            backend,
            runtime.config,
            draft=record.draft,
            critic=critic,
            messages=messages,
            article_listing=listing,
            title=record.title,
            folder=record.folder,
        )
        save_article_artifacts(runtime.config, record, write_final=True)
        articles[article_id] = record.model_dump()
    return {"articles": articles, "hierarchy": hierarchy.model_dump(), "status": "polished"}


def advance_node(state: PipelineState) -> dict[str, Any]:
    index = (state.get("work_index") or 0) + 1
    items = state.get("work_items") or []
    done = index >= len(items)
    return {
        "work_index": index,
        "current_messages": [],
        "touched_article_ids": [],
        "done": done,
        "status": "completed" if done else f"advance to work_index={index}",
    }


def _until(state: PipelineState) -> str:
    return state.get("until") or "polish"


def _rank(stage: str) -> int:
    try:
        return STAGE_ORDER.index(stage)
    except ValueError:
        return len(STAGE_ORDER) - 1


def after_ingest(state: PipelineState) -> str:
    if state.get("done"):
        return END
    if _rank(_until(state)) <= _rank("ingest"):
        return "advance"
    return "structure"


def after_structure(state: PipelineState) -> str:
    if _rank(_until(state)) <= _rank("structure"):
        return "advance"
    return "generate"


def after_generate(state: PipelineState) -> str:
    if _rank(_until(state)) <= _rank("generate"):
        return "advance"
    return "critic"


def after_critic(state: PipelineState) -> str:
    if _rank(_until(state)) <= _rank("critic"):
        return "advance"
    return "polish"


def after_advance(state: PipelineState) -> str:
    if state.get("done"):
        return END
    return "ingest"


def build_graph() -> StateGraph:
    graph = StateGraph(PipelineState)
    graph.add_node("ingest", ingest_node)
    graph.add_node("structure", structure_node)
    graph.add_node("generate", generate_node)
    graph.add_node("critic", critic_node)
    graph.add_node("polish", polish_node)
    graph.add_node("advance", advance_node)
    graph.add_edge(START, "ingest")
    graph.add_conditional_edges("ingest", after_ingest)
    graph.add_conditional_edges("structure", after_structure)
    graph.add_conditional_edges("generate", after_generate)
    graph.add_conditional_edges("critic", after_critic)
    graph.add_edge("polish", "advance")
    graph.add_conditional_edges("advance", after_advance)
    return graph


def _make_sqlite_saver(path: Path):
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise RuntimeError(
            "Install langgraph-checkpoint-sqlite to enable checkpoints"
        ) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(SqliteSaver, "from_conn_string"):
        return SqliteSaver.from_conn_string(str(path))
    conn = sqlite3.connect(str(path), check_same_thread=False)
    return SqliteSaver(conn)


@contextmanager
def compiled_graph(checkpoint_path: Path) -> Iterator[Any]:
    builder = build_graph()
    saver = _make_sqlite_saver(checkpoint_path)
    if hasattr(saver, "__enter__"):
        with saver as opened:
            yield builder.compile(checkpointer=opened)
    else:
        yield builder.compile(checkpointer=saver)
