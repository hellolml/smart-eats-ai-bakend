from __future__ import annotations

from typing import Any

from app.common.config import settings


def make_summarize_node():
    async def summarize_node(state: dict[str, Any], store: Any = None) -> dict[str, Any]:
        from app.agent.llm_adapters import OpenAIWriter
        from app.agent.runtime import builder as runtime_builder

        chat_state = runtime_builder._state_from_dict(state)
        messages = list(state.get("messages") or [])
        previous_summary = chat_state.summary
        keep_recent = max(2, int(len(messages) * settings.CHAT_COMPACT_TAIL_RATIO))
        keep_recent = max(keep_recent, 4)
        user_turn_count = sum(1 for message in messages if getattr(message, "type", None) == "human")
        keep_recent_turns = max(2, int(user_turn_count * settings.CHAT_COMPACT_TAIL_RATIO))
        removable = messages[: max(0, len(messages) - keep_recent)]
        if not removable:
            return runtime_builder._state_update(chat_state)

        prompt = runtime_builder.build_summary_prompt(previous_summary=previous_summary, messages=removable)
        chunks: list[str] = []
        try:
            writer = OpenAIWriter(provider=chat_state.provider or settings.LLM_PROVIDER)
            async for delta in writer.stream("你是对话摘要器。", prompt):
                chunks.append(delta)
            new_summary = "".join(chunks).strip()
        except Exception as exc:
            runtime_builder.logger.info("langgraph_summary_failed session_id=%s reason=%s", chat_state.session_id, str(exc))
            new_summary = ""
        if not new_summary:
            new_summary = prompt[:1600]
        normalized = runtime_builder.normalize_summary_output(new_summary)
        if not normalized.get("valid"):
            repair_chunks: list[str] = []
            repair_prompt = runtime_builder.build_summary_repair_prompt(raw_output=new_summary, original_prompt=prompt)
            try:
                writer = OpenAIWriter(provider=chat_state.provider or settings.LLM_PROVIDER)
                async for delta in writer.stream("你是对话摘要器。", repair_prompt):
                    repair_chunks.append(delta)
                repaired = "".join(repair_chunks).strip()
                if repaired:
                    repaired_normalized = runtime_builder.normalize_summary_output(repaired)
                    if repaired_normalized.get("valid"):
                        normalized = repaired_normalized
            except Exception as exc:
                runtime_builder.logger.info(
                    "langgraph_summary_repair_failed session_id=%s reason=%s",
                    chat_state.session_id,
                    str(exc),
                )

        update = runtime_builder.build_summary_update(
            messages,
            previous_summary=previous_summary,
            new_summary=normalized["summary"],
            keep_recent=keep_recent,
            keep_recent_turns=keep_recent_turns,
            summary_json=normalized["summary_json"],
            source_refs=chat_state.source_refs,
        )
        chat_state.summary = update["summary"]
        chat_state.context_budget = update["context_budget"]
        reduction = float(chat_state.context_budget.get("last_compaction_reduction_ratio") or 0.0)
        previous_attempts = int((state.get("context_budget") or {}).get("compact_attempts") or 0)
        chat_state.context_budget["compact_attempts"] = (
            previous_attempts + 1
            if reduction < settings.CHAT_COMPACT_MIN_REDUCTION_RATIO
            else 0
        )
        if isinstance((state.get("context_budget") or {}).get("active_context"), dict):
            chat_state.context_budget["active_context_before"] = (state.get("context_budget") or {}).get("active_context")
        memory_writes = await runtime_builder.persist_summary_memories(
            store,
            user_id=chat_state.user_id,
            summary_json=normalized["summary_json"],
        )
        if memory_writes:
            chat_state.context_budget["summary_memory_write_count"] = len(memory_writes)
        await runtime_builder.save_compaction_run(store, thread_id=chat_state.session_id, summary_update=update)
        output = runtime_builder._state_update(chat_state)
        output["messages"] = update["messages"]
        return output

    return summarize_node
