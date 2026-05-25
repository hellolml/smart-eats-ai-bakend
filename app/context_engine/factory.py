from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import settings
from app.context_engine.condenser import Condenser
from app.context_engine.engine import ContextEngine
from app.context_engine.memory import PgVectorMemoryStore
from app.context_engine.stores import SqlConversationStore
from app.context_engine.summarizers import LLMStructuredSummarizer, WriterCondenseModel
from app.context_engine.tokenizer import ApproxTokenCounter


def build_context_engine(
    *,
    db: AsyncSession,
    providers: list[object] | None = None,
    max_tokens: int | None = None,
) -> ContextEngine:
    token_counter = ApproxTokenCounter()
    conversation_store = SqlConversationStore(db)
    memory_store = PgVectorMemoryStore(db)
    condenser = Condenser(
        store=conversation_store,
        token_counter=token_counter,
        summarizer=LLMStructuredSummarizer(model=WriterCondenseModel(provider=settings.LLM_PROVIDER)),
        model=settings.LLM_PROVIDER,
        memory_store=memory_store,
    )
    return ContextEngine(
        conversation_store=conversation_store,
        memory_store=memory_store,
        token_counter=token_counter,
        providers=providers or [],
        max_tokens=max_tokens or settings.LLM_MODEL_CONTEXT_SIZE,
        condenser=condenser,
    )
