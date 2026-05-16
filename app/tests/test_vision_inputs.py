from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_minio_stub_reads_uploaded_bytes(tmp_path: Path):
    from app.infra.minio import MinioStub

    minio = MinioStub(base_path=tmp_path, bucket="smart-eats")
    await minio.upload_bytes("chat/u1/s1/guide.png", b"image-bytes")

    assert await minio.read_bytes("chat/u1/s1/guide.png") == b"image-bytes"


@pytest.mark.asyncio
async def test_build_vision_content_parts_reads_image_attachments():
    from app.agent.vision import build_vision_content_parts

    class FakeMinio:
        async def read_bytes(self, object_key: str) -> bytes:
            assert object_key == "chat/u1/s1/guide.png"
            return b"fake-png"

    parts = await build_vision_content_parts(
        [
            {
                "kind": "image",
                "object_key": "chat/u1/s1/guide.png",
                "content_type": "image/png",
                "size_bytes": 8,
            }
        ],
        minio=FakeMinio(),
    )

    assert parts == [
        {
            "type": "image_url",
            "image_url": {
                "url": "data:image/png;base64,ZmFrZS1wbmc=",
            },
        }
    ]


@pytest.mark.asyncio
async def test_planner_uses_multimodal_message_and_vision_model():
    from app.agent.llm_adapters import OpenAIPlanner, ProviderConfig

    captured: dict = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="我看到了西湖和灵隐寺", tool_calls=[])
                    )
                ]
            )

    planner = OpenAIPlanner.__new__(OpenAIPlanner)
    planner.config = ProviderConfig(
        name="qwen",
        api_key="test",
        base_url="https://example.test/v1",
        model_planner="qwen-text",
        model_writer="qwen-text",
        model_vision_planner="qwen-vl",
    )
    planner.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    decision = await planner.plan_tool_calls(
        "system",
        "请识别图片中的旅行地点",
        [],
        image_parts=[
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,ZmFrZQ=="},
            }
        ],
    )

    assert captured["model"] == "qwen-vl"
    assert captured["messages"][1]["content"] == [
        {"type": "text", "text": "请识别图片中的旅行地点"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,ZmFrZQ=="}},
    ]
    assert decision["content"] == "我看到了西湖和灵隐寺"
