from __future__ import annotations

import pytest

from app.agent.agents.smart_eats import _initialize_graph_state
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


def test_initialize_graph_state_preserves_checkpoint_resume_shape():
    payload = {
        "session_id": "s-checkpoint",
        "message": "继续",
        "resume_from_checkpoint": True,
        "checkpoint_ref": "cp_1",
        "replay_from_checkpoint": True,
        "resume_payload": {"message": "继续上次"},
    }

    graph_state = _initialize_graph_state(payload)

    assert graph_state["resume_from_checkpoint"] is True
    assert graph_state["checkpoint_ref"] == "cp_1"
    assert graph_state["replay_from_checkpoint"] is True
    assert graph_state["resume_payload"] == {"message": "继续上次"}
    assert graph_state["messages"] == []
    assert "_tool_messages" not in graph_state
    assert "_tool_call_args" not in graph_state


@pytest.mark.asyncio
async def test_build_chat_state_uses_model_override_when_present(override_redis):
    model_value = AppBffService.list_chat_models()["default"]
    state = await AppBffService.build_chat_state(
        session_id="s-model-override",
        user_id="u1",
        payload={
            "message": "帮我推荐晚饭",
            "model": model_value,
        },
        request_client_ip="127.0.0.1",
        redis_client=override_redis,
        rate_limit_key_prefix="chat_test",
    )

    assert state.provider == model_value


@pytest.mark.asyncio
async def test_build_chat_state_accepts_scene_override(override_redis):
    state = await AppBffService.build_chat_state(
        session_id="s-travel",
        user_id="u1",
        payload={
            "message": "帮我规划杭州三天两晚旅行",
            "scene": "travel_planner",
        },
        request_client_ip="127.0.0.1",
        redis_client=override_redis,
        rate_limit_key_prefix="chat_test",
    )

    assert state.scene == "travel_planner"


@pytest.mark.asyncio
async def test_build_chat_state_preserves_attachments_in_context(override_redis):
    attachment = {
        "attachment_id": "att_1",
        "kind": "image",
        "object_key": "chat/u1/s-travel/guide.png",
        "filename": "guide.png",
        "content_type": "image/png",
        "size_bytes": 10,
    }

    state = await AppBffService.build_chat_state(
        session_id="s-travel",
        user_id="u1",
        payload={
            "message": "请从这张攻略截图提取地点",
            "scene": "travel_planner",
            "attachments": [attachment],
        },
        request_client_ip="127.0.0.1",
        redis_client=override_redis,
        rate_limit_key_prefix="chat_test",
    )

    assert state.context_overrides["attachments"] == [attachment]


@pytest.mark.asyncio
async def test_create_chat_attachment_uploads_image_metadata():
    class FakeMinio:
        def __init__(self):
            self.uploaded = []

        async def upload_bytes(self, object_key, data):
            self.uploaded.append((object_key, data))
            return object_key

    minio = FakeMinio()

    data = await AppBffService.create_chat_attachment(
        user_id="u1",
        session_id="s-travel",
        filename="guide.png",
        content_type="image/png",
        content=b"fake-image",
        minio=minio,
    )

    assert data["kind"] == "image"
    assert data["filename"] == "guide.png"
    assert data["content_type"] == "image/png"
    assert data["size_bytes"] == len(b"fake-image")
    assert data["object_key"].startswith("chat/u1/s-travel/")
    assert minio.uploaded == [(data["object_key"], b"fake-image")]
