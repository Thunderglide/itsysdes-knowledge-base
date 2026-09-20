# Graph Report - itsysdes-knowledge-base  (2026-09-20)

## Corpus Check
- 73 files · ~30,414 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 617 nodes · 2252 edges · 37 communities (15 shown, 3 thin omitted)
- Extraction: 84% EXTRACTED · 16% INFERRED · 0% AMBIGUOUS · INFERRED: 357 edges (avg confidence: 0.94)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `d4d77d31`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- source.py
- persist.py
- FakeBackend
- graph.py
- test_curate.py
- Hierarchy
- config.py
- Config
- extract_json_object
- LLMBackend
- kb_pipeline/__init__.py
- kb-pipeline
- relink.py
- kb-pipeline
- merger.py
- content_stitch.py
- lmstudio.py
- 0.2.0 — 2026-09-20

## God Nodes (most connected - your core abstractions)
1. `Config` - 124 edges
2. `Hierarchy` - 90 edges
3. `Message` - 43 edges
4. `Article` - 42 edges
5. `load_article_record()` - 33 edges
6. `Folder` - 32 edges
7. `MessageRef` - 27 edges
8. `apply_plan()` - 26 edges
9. `LLMBackend` - 25 edges
10. `main()` - 22 edges

## Surprising Connections (you probably didn't know these)
- `test_agent_fallback_uses_structure()` --uses--> `Config`  [INFERRED]
  tests/test_curate.py → src/kb_pipeline/config.py
- `test_critic_report_coerces_question_objects()` --uses--> `CriticReport`  [INFERRED]
  tests/test_models.py → src/kb_pipeline/models.py
- `test_parse_markdown_sections_collects_citations()` --calls--> `parse_markdown_sections()`  [EXTRACTED]
  tests/test_curate.py → src/kb_pipeline/agents/splitter.py
- `test_openai_client_disables_retries_and_uses_long_timeout()` --uses--> `LMStudioSettings`  [INFERRED]
  tests/test_lmstudio.py → src/kb_pipeline/config.py
- `test_validate_merge_skips_dump_and_non_stubs()` --uses--> `CurateSettings`  [INFERRED]
  tests/test_curate.py → src/kb_pipeline/config.py

## Import Cycles
- None detected.

## Communities (37 total, 3 thin omitted)

### Community 0 - "source.py"
Cohesion: 0.10
Nodes (49): LookupError, regenerate_articles(), build_work_items(), filter_part_indices(), list_subchats(), _match_subchat(), resolve_subchats(), SubchatNotFoundError (+41 more)

### Community 1 - "persist.py"
Cohesion: 0.12
Nodes (47): ArgumentParser, Namespace, _article_matches_session(), clean_checkpoints(), clean_kb(), clean_session(), collect_checkpoint_targets(), collect_kb_targets() (+39 more)

### Community 2 - "FakeBackend"
Cohesion: 0.07
Nodes (33): AgentLLMConfig, DeepSeekSettings, LMStudioSettings, BaseModel, _choice_text(), _completion_budget(), DeepSeekBackend, DeepSeekError (+25 more)

### Community 3 - "graph.py"
Cohesion: 0.21
Nodes (29): RunnableConfig, advance_node(), after_advance(), after_critic(), after_generate(), after_ingest(), after_structure(), _article_messages() (+21 more)

### Community 4 - "test_curate.py"
Cohesion: 0.12
Nodes (50): main(), ingest_part(), Path, source_message_id(), add_attachment(), add_chat(), add_message(), create_db() (+42 more)

### Community 5 - "Hierarchy"
Cohesion: 0.09
Nodes (58): run_structure(), Article, coerce_hierarchy(), Folder, Hierarchy, _merge_folders(), merge_hierarchy(), _merge_message_refs() (+50 more)

### Community 6 - "config.py"
Cohesion: 0.13
Nodes (37): complete_json(), complete_text(), LLMError, messages_payload(), Any, RuntimeError, run_critic(), run_curate_polish() (+29 more)

### Community 7 - "Config"
Cohesion: 0.10
Nodes (51): _pick_duplicate_target(), propose_cleanup(), Any, _slug_from_id(), _unique(), validate_tag_actions(), Config, Path (+43 more)

### Community 8 - "extract_json_object"
Cohesion: 0.21
Nodes (15): _as_object(), _close_truncated_json(), extract_json_model(), extract_json_object(), _extract_payload(), _payload_candidates(), Any, _strip_incomplete_key() (+7 more)

### Community 13 - "relink.py"
Cohesion: 0.14
Nodes (34): save_hierarchy(), aliases_from_plan(), aliases_path(), _apply_mapping(), apply_relink_plan(), article_rel_path(), href_fragment(), href_path() (+26 more)

### Community 14 - "kb-pipeline"
Cohesion: 0.12
Nodes (16): 1. `.env` (секреты), 2. `config.yaml` (локальные пути), 3. `.gitignore`, DeepSeek, Graphify (карта репозитория для Cursor), kb-pipeline, LM Studio и LM Link, macOS и Linux (+8 more)

### Community 27 - "merger.py"
Cohesion: 0.23
Nodes (20): normalize_title(), _action_from_cluster(), _batches(), _best_parent(), _chunk_by_messages(), _cluster_key(), _deterministic_clusters(), _llm_merge_batch() (+12 more)

### Community 28 - "content_stitch.py"
Cohesion: 0.16
Nodes (23): Any, _tag_row(), citation_ids(), citations_lost(), _drop_h1(), extract_part_markdown(), first_heading(), MarkdownSection (+15 more)

### Community 35 - "lmstudio.py"
Cohesion: 0.15
Nodes (25): OpenAI, _first_lmstudio_model(), _is_no_models_error(), _is_response_format_error(), _is_timeout_error(), _list_model_ids(), LMStudioBackend, LMStudioError (+17 more)

### Community 36 - "0.2.0 — 2026-09-20"
Cohesion: 0.29
Nodes (6): 0.2.0 — 2026-09-20, Changelog, Куратор (`kb-pipeline curate`), Очистка, Пайплайн и LLM, Прочее

## Knowledge Gaps
- **17 isolated node(s):** `kb-pipeline`, `Пайплайн и LLM`, `Куратор (`kb-pipeline curate`)`, `Очистка`, `Прочее` (+12 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 68 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Config` connect `Config` to `source.py`, `persist.py`, `FakeBackend`, `graph.py`, `test_curate.py`, `Hierarchy`, `config.py`, `lmstudio.py`, `relink.py`, `merger.py`, `content_stitch.py`?**
  _High betweenness centrality (0.234) - this node is a cross-community bridge._
- **Why does `Hierarchy` connect `Hierarchy` to `source.py`, `persist.py`, `FakeBackend`, `graph.py`, `test_curate.py`, `config.py`, `Config`, `relink.py`, `merger.py`, `content_stitch.py`?**
  _High betweenness centrality (0.097) - this node is a cross-community bridge._
- **Why does `Message` connect `Hierarchy` to `source.py`, `graph.py`, `test_curate.py`, `config.py`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Are the 93 inferred relationships involving `Config` (e.g. with `propose_cleanup()` and `run_critic()`) actually correct?**
  _`Config` has 93 INFERRED edges - model-reasoned connections that need verification._
- **Are the 66 inferred relationships involving `Hierarchy` (e.g. with `propose_cleanup()` and `_llm_merge_batch()`) actually correct?**
  _`Hierarchy` has 66 INFERRED edges - model-reasoned connections that need verification._
- **Are the 29 inferred relationships involving `Message` (e.g. with `messages_payload()` and `run_critic()`) actually correct?**
  _`Message` has 29 INFERRED edges - model-reasoned connections that need verification._
- **Are the 28 inferred relationships involving `Article` (e.g. with `apply_merge_actions()` and `_merge_message_refs()`) actually correct?**
  _`Article` has 28 INFERRED edges - model-reasoned connections that need verification._