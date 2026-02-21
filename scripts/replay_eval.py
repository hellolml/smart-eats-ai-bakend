#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_cases(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def is_fallback(answer: dict[str, Any]) -> bool:
    recs = answer.get("recommendations") if isinstance(answer, dict) else None
    if not isinstance(recs, list):
        return False
    for item in recs:
        if isinstance(item, dict) and str(item.get("reason") or "") == "fallback":
            return True
    return False


def run_case(base_url: str, case: dict[str, Any]) -> dict[str, Any]:
    import httpx

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        r = client.post("/api/v1/chat/sessions")
        r.raise_for_status()
        session_id = r.json()["data"]["session_id"]

        resp = client.post(
            f"/api/v1/chat/sessions/{session_id}/stream",
            json={"message": case["message"]},
            headers={"accept": "text/event-stream"},
        )
        resp.raise_for_status()

        final_payload: dict[str, Any] | None = None
        event = None
        for line in resp.text.splitlines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event == "final":
                data = line.split(":", 1)[1].strip()
                try:
                    final_payload = json.loads(data)
                except json.JSONDecodeError:
                    pass

        answer = ((final_payload or {}).get("answer") or {}) if isinstance(final_payload, dict) else {}
        return {
            "id": case.get("id"),
            "message": case.get("message"),
            "fallback": is_fallback(answer),
            "answer": answer,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay chat cases against local SmartEats backend")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument(
        "--cases",
        default="app/tests/fixtures/replay_cases.json",
        help="Replay cases JSON path",
    )
    parser.add_argument("--out", default="replay_report.json", help="Output JSON report path")
    args = parser.parse_args()

    cases = load_cases(Path(args.cases))
    results = [run_case(args.base_url, case) for case in cases]

    total = len(results)
    fallback_count = sum(1 for x in results if x.get("fallback"))

    report = {
        "total": total,
        "fallback_count": fallback_count,
        "fallback_rate": (fallback_count / total) if total else 0.0,
        "results": results,
    }

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
