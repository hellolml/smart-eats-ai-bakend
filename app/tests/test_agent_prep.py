from __future__ import annotations

import pytest

from app.domain.app import agent_prep


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)


class _FakeMessage:
    def __init__(self, answer):
        self.tool_payload_json = {"answer": answer}


class _FakeDb:
    def __init__(self, rows=None):
        self.rows = rows or [
            _FakeMessage(
                {
                    "state": "candidates_ready",
                    "plan_type": "travel",
                    "candidates": [{"name": "西湖"}],
                }
            )
        ]

    async def execute(self, _statement):
        return _FakeResult(self.rows)


@pytest.mark.asyncio
async def test_prepare_supervisor_payload_uses_structured_agent_context():
    payload = await agent_prep.prepare_supervisor_payload(
        _FakeDb(),
        "s1",
        None,
        {
            "message": "继续旅行计划",
            "client_context_overrides": {"environment": {"location": {"lat": 30.2}}},
        },
    )

    overrides = payload["client_context_overrides"]
    assert overrides["environment"]["location"]["lat"] == 30.2
    assert overrides["latest_travel_final_json"]["state"] == "candidates_ready"


@pytest.mark.asyncio
async def test_prepare_supervisor_payload_recovers_recent_restaurants_from_history():
    db = _FakeDb(
        [
            _FakeMessage(
                {
                    "recommendations": [
                        {
                            "type": "restaurant",
                            "title": "五星之家",
                            "raw": {"name": "五星之家", "address": "洋湖附近", "rating": "4.5"},
                        }
                    ]
                }
            )
        ]
    )

    payload = await agent_prep.prepare_supervisor_payload(
        db,
        "s1",
        None,
        {"message": "五星之家", "client_context_overrides": {}},
    )

    overrides = payload["client_context_overrides"]
    assert overrides["last_restaurants"][0]["name"] == "五星之家"
    assert overrides["intent"] == "eat_out"
    assert "restaurant_finder" in overrides["forced_skill_ids"]


@pytest.mark.asyncio
async def test_prepare_supervisor_payload_extracts_nested_restaurant_coordinates():
    db = _FakeDb(
        [
            _FakeMessage(
                {
                    "recommendations": [
                        {
                            "type": "restaurant",
                            "title": "京味烤鸭",
                            "raw": {
                                "name": "京味烤鸭",
                                "raw": {
                                    "address": "天安门附近",
                                    "location": {"longitude": 116.397, "latitude": 39.905},
                                },
                            },
                        }
                    ]
                }
            )
        ]
    )

    payload = await agent_prep.prepare_supervisor_payload(
        db,
        "s1",
        None,
        {"message": "从天安门到第一家怎么走", "client_context_overrides": {}},
    )

    restaurant = payload["client_context_overrides"]["last_restaurants"][0]
    assert restaurant["lat"] == 39.905
    assert restaurant["lng"] == 116.397
    assert restaurant["address"] == "天安门附近"


@pytest.mark.asyncio
async def test_prepare_supervisor_payload_keeps_selected_restaurant_route_followup_in_route_context():
    db = _FakeDb(
        [
            _FakeMessage(
                {
                    "selected_restaurant": {
                        "name": "安庆馄饨董家金牌锅贴",
                        "raw": {
                            "name": "安庆馄饨董家金牌锅贴",
                            "raw": {
                                "address": "丰富路店",
                                "location": {"longitude": 118.784, "latitude": 32.041},
                            },
                        },
                    },
                    "recommendations": [
                        {
                            "type": "restaurant",
                            "title": "安庆馄饨董家金牌锅贴",
                            "raw": {
                                "name": "安庆馄饨董家金牌锅贴",
                                "raw": {
                                    "address": "丰富路店",
                                    "location": {"longitude": 118.784, "latitude": 32.041},
                                },
                            },
                        }
                    ],
                }
            )
        ]
    )

    payload = await agent_prep.prepare_supervisor_payload(
        db,
        "s1",
        None,
        {"message": "还是回到刚才选的那家餐厅，从新街口过去怎么走？", "client_context_overrides": {}},
    )

    overrides = payload["client_context_overrides"]
    assert overrides["selected_restaurant"]["lat"] == 32.041
    assert overrides["selected_restaurant"]["lng"] == 118.784
    assert overrides.get("intent") != "eat_out"
    assert "food_decision" not in overrides.get("forced_skill_ids", [])


@pytest.mark.asyncio
async def test_prepare_supervisor_payload_prefers_coordinate_rich_restaurant_history():
    db = _FakeDb(
        [
            _FakeMessage(
                {
                    "selected_restaurant": {
                        "provider_id": "B001",
                        "name": "LE COQ 大公鸡小酒馆",
                        "geo": None,
                    },
                    "recommendations": [
                        {
                            "type": "restaurant",
                            "title": "LE COQ 大公鸡小酒馆",
                            "raw": {"provider_id": "B001", "name": "LE COQ 大公鸡小酒馆", "geo": None},
                        }
                    ],
                }
            ),
            _FakeMessage(
                {
                    "recommendations": [
                        {
                            "type": "restaurant",
                            "title": "LE COQ 大公鸡小酒馆",
                            "raw": {
                                "provider_id": "B001",
                                "name": "LE COQ 大公鸡小酒馆",
                                "raw": {
                                    "location": {"longitude": 116.454266, "latitude": 39.935577},
                                    "address": "三里屯太古里",
                                },
                            },
                        }
                    ]
                }
            ),
        ]
    )

    payload = await agent_prep.prepare_supervisor_payload(
        db,
        "s1",
        None,
        {"message": "从三里屯太古里过去怎么走？", "client_context_overrides": {}},
    )

    selected = payload["client_context_overrides"]["selected_restaurant"]
    assert selected["lat"] == 39.935577
    assert selected["lng"] == 116.454266


@pytest.mark.asyncio
async def test_prepare_supervisor_payload_keeps_home_chef_refinement_in_worker():
    db = _FakeDb(
        [
            _FakeMessage(
                {
                    "scene": "home_chef",
                    "agent_id": "home_chef",
                    "recommendations": [{"type": "note", "title": "豆腐青菜鸡蛋 15 分钟方案"}],
                }
            )
        ]
    )

    payload = await agent_prep.prepare_supervisor_payload(
        db,
        "s1",
        None,
        {"message": "不要辣，蛋白质要够，步骤具体一点。", "client_context_overrides": {}},
    )

    overrides = payload["client_context_overrides"]
    assert overrides["intent"] == "cook_home"
    assert "home_chef" in overrides["forced_skill_ids"]
    assert overrides["latest_home_chef_final_json"]["scene"] == "home_chef"


@pytest.mark.asyncio
async def test_prepare_supervisor_payload_does_not_force_home_chef_on_eat_out_switch():
    db = _FakeDb(
        [
            _FakeMessage(
                {
                    "scene": "home_chef",
                    "agent_id": "home_chef",
                    "recommendations": [{"type": "note", "title": "牛肉土豆 30 分钟方案"}],
                }
            )
        ]
    )

    payload = await agent_prep.prepare_supervisor_payload(
        db,
        "s1",
        None,
        {"message": "但是我突然不想做饭了，在成都春熙路附近找不辣的牛肉类餐厅，人均 90。", "client_context_overrides": {}},
    )

    overrides = payload["client_context_overrides"]
    assert overrides["latest_home_chef_final_json"]["scene"] == "home_chef"
    assert overrides.get("intent") != "cook_home"
    assert "home_chef" not in overrides.get("forced_skill_ids", [])


@pytest.mark.asyncio
async def test_prepare_supervisor_payload_matches_partial_restaurant_hint():
    payload = await agent_prep.prepare_supervisor_payload(
        _FakeDb(
            [
                _FakeMessage(
                    {
                        "recommendations": [
                            {"type": "restaurant", "title": "五星之家"},
                            {"type": "restaurant", "title": "屋门口土菜研究院"},
                        ]
                    }
                )
            ]
        ),
        "s1",
        None,
        {"message": "就选你上面推荐里名字带“五星”的那家。", "client_context_overrides": {}},
    )

    assert payload["client_context_overrides"]["intent"] == "eat_out"
