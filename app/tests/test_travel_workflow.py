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


def test_travel_candidate_service_resets_stale_artifacts_on_trip_revision():
    latest = {
        "state": "candidates_ready",
        "trip_meta": {"destination": "苏州", "days": 2},
        "candidates": [{"name": "拙政园"}, {"name": "苏州博物馆"}],
        "failed_places": [{"name": "七里山塘"}],
        "itinerary": {"days": [{"day_number": 1}]},
        "map": {"qr_code_url": "old"},
    }
    payload = TravelCandidateService.context_from_final_json(latest)

    revised = TravelCandidateService.apply_revision_from_message(
        "临时改成杭州 1 天，不去拙政园，只保留西湖和灵隐寺，别太赶。",
        payload,
        latest=latest,
    )

    assert revised["state"] == "ingesting_content"
    assert revised["refresh_sources"] is True
    assert revised["trip_meta"]["destination"] == "杭州"
    assert revised["trip_meta"]["days"] == 1
    assert [item["name"] for item in revised["extracted_places"]] == ["西湖", "灵隐寺"]
    assert [item["name"] for item in revised["excluded_places"]] == ["拙政园"]
    assert "candidates" not in revised
    assert "failed_places" not in revised
    assert "itinerary" not in revised
    assert "map" not in revised
    assert revised["previous_candidates"] == latest["candidates"]
    assert revised["stale_artifacts"]["reason"] == "trip_revision"


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


def test_travel_source_ingestion_extracts_structured_text_without_prompt_artifacts():
    message = (
        "目的地：成都\n"
        "出行时间：2026-06-10\n"
        "出行天数：三天 2 晚 天\n"
        "出行人数：1 人\n"
        "我想去：宽窄巷子、武侯祠、杜甫草堂。\n"
        "请输出清晰的候选旅行行程，包含每日路线、景点顺序和必要提醒。\n\n"
        "请先输出候选行程，等待用户确认后由应用层创建计划记录。不要直接操作数据库。"
    )

    payload = TravelSourceIngestionService.ingest_text_request(message, {})

    assert payload["state"] == "ingesting_content"
    assert payload["trip_meta"]["destination"] == "成都"
    assert payload["trip_meta"]["days"] == 3
    assert payload["trip_meta"]["travelers_count"] == 1
    assert [item["name"] for item in payload["extracted_places"]] == ["宽窄巷子", "武侯祠", "杜甫草堂"]
    assert all("请" not in item["name"] for item in payload["extracted_places"])


def test_travel_rough_itinerary_keeps_requested_day_count_with_sparse_candidates():
    from agent_skills.travel_plan_new.hooks import _build_rough_itinerary

    itinerary = _build_rough_itinerary(
        [
            {
                "name": "宽窄巷子",
                "poi": {
                    "poi_id": "poi-kuanzhai",
                    "longitude": 104.043,
                    "latitude": 30.67,
                },
            }
        ],
        {"destination": "成都", "days": 3},
    )

    assert len(itinerary["days"]) == 3
    assert itinerary["days"][0]["items"][0]["place_name"] == "宽窄巷子"
    assert itinerary["days"][1]["items"][0]["place_name"] == "轻松留白"
    assert itinerary["days"][2]["items"][0]["place_name"] == "轻松留白"


def test_travel_name_dedupe_ignores_cn_punctuation():
    payload = TravelSourceIngestionService.ingest_text_request(
        "目的地：成都\n出行天数：三天\n我想去：杜甫草堂、杜甫草堂。",
        {},
    )

    assert [item["name"] for item in payload["extracted_places"]] == ["杜甫草堂"]
