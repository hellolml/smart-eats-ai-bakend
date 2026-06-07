from __future__ import annotations

from app.domain.travel.workflow import TravelCandidateService, TravelSourceIngestionService


def test_travel_candidate_context_from_final_json_preserves_previous_state():
    latest = {
        "state": "candidates_ready",
        "candidates": [{"name": "西湖"}],
        "itinerary": {},
        "map": None,
    }

    payload = TravelCandidateService.context_from_final_json(latest)

    assert payload["previous_final_json"] == latest
    assert payload["state"] == "candidates_ready"
    assert payload["candidates"] == [{"name": "西湖"}]
    assert "itinerary" not in payload
    assert "map" not in payload


def test_travel_candidate_service_infers_confirm_and_map_actions():
    assert (
        TravelCandidateService.infer_action_from_message(
            "确认这些候选地点，请继续生成最终每日行程。",
            latest={"state": "candidates_ready", "candidates": [{"name": "西湖"}]},
        )
        == "confirm_candidates"
    )
    assert (
        TravelCandidateService.infer_action_from_message(
            "生成地图",
            latest={
                "state": "itinerary_generated",
                "itinerary": {"days": [{"day_number": 1}]},
            },
        )
        == "generate_map"
    )


def test_travel_source_ingestion_marks_refresh_and_stales_artifacts():
    latest = {
        "state": "itinerary_generated",
        "itinerary": {"days": [{"day": 1}]},
        "map": {"qr_code_url": "old"},
    }
    payload = {"new_attachments": [{"attachment_id": "a1"}], "itinerary": {"days": []}, "map": {"qr_code_url": "new"}}

    marked = TravelSourceIngestionService.mark_refresh_sources(payload, latest)

    assert marked["state"] == "ingesting_content"
    assert marked["refresh_sources"] is True
    assert marked["previous_itinerary"] == {"days": [{"day": 1}]}
    assert marked["previous_map"] == {"qr_code_url": "old"}
    assert marked["stale_artifacts"] == {"itinerary": True, "map": True, "reason": "new_attachments"}
    assert "itinerary" not in marked
    assert "map" not in marked
