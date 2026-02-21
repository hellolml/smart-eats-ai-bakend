from __future__ import annotations

from fastapi import APIRouter

from app.agent.graph import get_agent_metrics_snapshot, reset_agent_metrics

router = APIRouter()


@router.get("/metrics/agent")
async def get_agent_metrics(reset: bool = False):
    stats = get_agent_metrics_snapshot()
    fallback = int(stats.get("fallback_final", 0))
    non_fallback = int(stats.get("non_fallback_final", 0))
    total_final = fallback + non_fallback
    fallback_rate = (fallback / total_final) if total_final > 0 else 0.0

    payload = {
        "metrics": stats,
        "summary": {
            "total_final": total_final,
            "fallback_rate": fallback_rate,
        },
    }
    if reset:
        reset_agent_metrics()
    return payload
