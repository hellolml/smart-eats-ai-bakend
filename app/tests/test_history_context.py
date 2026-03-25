from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent.agents.smart_eats import _refresh_observation_context, get_smart_eats_agent_config
from app.agent.state import ChatState
from app.infra.db import AsyncSessionLocal
from app.infra.models.chat import ChatMessage, ChatSession


@pytest.mark.asyncio
async def test_refresh_observation_context_loads_prior_history_before_save(override_redis):
    session_id = str(uuid4())
    previous_message = "上一次的路线请求"
    current_message = "我在黄鹤小区五片10栋，重新规划路线"

    async with AsyncSessionLocal() as db:
        db.add(
            ChatSession(
                id=session_id,
                user_id=None,
                scene="chat",
                title="测试会话",
            )
        )
        db.add(
            ChatMessage(
                id=str(uuid4()),
                session_id=session_id,
                role="user",
                content=previous_message,
            )
        )
        await db.commit()

        # Simulate cache expiration; only DB should contain prior history.
        await override_redis.delete(
            f"chat:history:{session_id}",
            f"chat:history:{session_id}:sig",
        )

        state = ChatState(
            session_id=session_id,
            user_id=None,
            message=current_message,
        )
        await _refresh_observation_context(
            db=db,
            redis_client=override_redis,
            state=state,
            agent_config=get_smart_eats_agent_config(),
            emit_context_event=False,
        )

        assert any(
            item.get("role") == "user" and item.get("content") == previous_message
            for item in state.history
        )
        assert all(
            not (item.get("role") == "user" and item.get("content") == current_message)
            for item in state.history
        )
        assert state.turn_index == 2

        result = await db.execute(
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.role == "user",
                ChatMessage.content == current_message,
            )
        )
        assert result.scalar_one_or_none() is not None
