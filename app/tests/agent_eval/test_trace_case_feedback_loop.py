"""Production trace to active regression case feedback loop tests."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.agent.monitoring import create_dataset_case_from_trace, review_dataset_case
from app.infra.models.eval import ConversationRun
from evals.persistence.postgres import EvalPersistenceStore
from evals.runners.harness import EvalHarness, HarnessConfig


@pytest.mark.asyncio
async def test_trace_case_can_be_reviewed_active_and_loaded_by_harness(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'eval.db'}"
    store = EvalPersistenceStore(database_url)
    try:
        await store.init_schema()
        async with store.session() as session:
            session.add(
                ConversationRun(
                    id="prod-run-1",
                    session_id="prod-session-1",
                    user_id="user-1",
                    trace_id="trace-prod-1",
                    scene="chat",
                    worker="chat_worker",
                    status="completed",
                    started_at=datetime.now(timezone.utc),
                    ended_at=datetime.now(timezone.utc),
                    final_state="completed",
                    raw_json={"user_message": "帮我总结今天吃什么"},
                )
            )
            await session.flush()

            created = await create_dataset_case_from_trace(
                session,
                run_id="prod-run-1",
                dataset_name="regression",
                version="draft",
                priority="p1",
                category="regression",
                owner="reviewer-1",
                review_status="draft",
            )
            assert created is not None
            assert created["review_status"] == "draft"

            reviewed = await review_dataset_case(
                session,
                dataset_name="regression",
                version="draft",
                case_id="prod-prod-run-1",
                decision="active",
                reviewer="reviewer-1",
                notes="加入回归集",
            )
            await session.commit()

        assert reviewed is not None
        assert reviewed["review_status"] == "active"

        harness = EvalHarness(
            HarnessConfig(
                suite="regression",
                runner="fixture",
                eval_database_url=database_url,
            )
        )
        cases = await harness._load_cases_for_run("regression")

        assert [case.id for case in cases] == ["prod-prod-run-1"]
        assert cases[0].tags == ["production_trace"]
        assert cases[0].expectations["source_trace"]["trace_id"] == "trace-prod-1"
    finally:
        await store.close()
