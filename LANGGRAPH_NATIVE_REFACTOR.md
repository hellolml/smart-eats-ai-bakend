# SmartEats LangGraph-native Runtime

## Current State

SmartEats now uses a LangGraph-native runtime:

```text
prepare -> summarize? -> agent -> tools -> agent -> END
```

- `messages` is the short-term conversation state and uses LangGraph `add_messages`.
- `checkpointer` persists per-thread graph state for resume and durable execution.
- `store` persists cross-thread data such as long-term memories, source events, and compaction debug records.
- Runtime context is temporarily injected into model input and is not written back into `messages`.

## Runtime Responsibilities

| Part | Responsibility |
|---|---|
| `prepare` | Normalize input, ensure chat session, load cached business context, retrieve LangGraph store memories, build temporary system prompt |
| `summarize` | Replace old messages with `RemoveMessage` and persist compaction metadata to store |
| `agent` | Call the planner with native LangChain messages and produce `AIMessage.tool_calls` or `final_json` |
| `tools` | Execute LangGraph `ToolNode`, save product chat history, store source events, refresh business context |

The old `initialize -> observe -> think -> tools -> tool_postprocess -> finalize` graph has been removed.

## Checkpoint vs Store

- `LANGGRAPH_CHECKPOINT_BACKEND` controls the per-thread state backend. SQLite is valid for local durable resume; Postgres is also supported.
- `LANGGRAPH_STORE_BACKEND` controls long-term data. Production should use `postgres`.
- `memory` store is for tests and local scratch sessions only. It is not long-term memory.

The graph is compiled with both:

```python
graph.compile(checkpointer=checkpointer, store=store)
```

## Removed Legacy Runtime

The old Context Engine and DB-memory paths are no longer part of the codebase:

- `app/context_engine/`
- `app/agent/memory.py`
- `app/agent/chat_history_compactor.py`
- `app/infra/models/context_engine.py`
- `app/infra/models/memory.py`

`app/agent/history.py` remains, but only for product chat history persistence and cache invalidation.

## Verification

Expected checks:

```bash
/opt/miniconda3/envs/smarteats/bin/python -m pytest app/tests -q
rg "context_engine|_refresh_observation_context|maybe_compress_history|UserMemoryStoreAdapter|chat_history_compactor" app app/tests
```

The second command should return no runtime or test references.
