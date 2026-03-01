from __future__ import annotations

import pytest

from app.domain.app.service import AppBffService


@pytest.mark.asyncio
async def test_build_chat_state_accepts_checkpoint_fields(override_redis):
    state = await AppBffService.build_chat_state(
        session_id="s-checkpoint",
        user_id="u1",
        payload={
            "message": "继续",
            "resume_from_checkpoint": True,
            "checkpoint_ref": "cp_1",
            "replay_from_checkpoint": True,
            "resume_payload": {"message": "继续上次"},
        },
        request_client_ip="127.0.0.1",
        redis_client=override_redis,
        rate_limit_key_prefix="chat_test",
    )

    assert state.resume_from_checkpoint is True
    assert state.checkpoint_ref == "cp_1"
    assert state.replay_from_checkpoint is True
    assert state.resume_payload == {"message": "继续上次"}


@pytest.mark.asyncio
async def test_build_chat_state_uses_model_override_when_present(override_redis):
    state = await AppBffService.build_chat_state(
        session_id="s-model-override",
        user_id="u1",
        payload={
            "message": "帮我推荐晚饭",
            "model": "qwen:qwen3.5-flash",
        },
        request_client_ip="127.0.0.1",
        redis_client=override_redis,
        rate_limit_key_prefix="chat_test",
    )

    assert state.provider == "qwen:qwen3.5-flash"
