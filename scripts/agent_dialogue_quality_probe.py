#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "food_restaurant_context_long",
        "turns": [
            {
                "message": "你好，先简单打个招呼。",
                "scene": "chat",
                "expect": {"worker": "general_chat", "no_tool_calls": True, "status_in": ["completed"]},
            },
            {
                "message": "我在长沙洋湖附近，想出去吃湘菜，人均 80 左右，帮我找几家。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision"],
                    "min_restaurant_recommendations": 1,
                },
            },
            {
                "message": "就选你上面推荐里名字带“五星”的那家。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["needs_clarification"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision"],
                    "answer_contains_any": ["五星", "没有", "当前可选"],
                },
            },
            {
                "message": "那从我现在的位置怎么走？",
                "scene": "chat",
                "expect": {
                    "worker": "route_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "search_restaurants"],
                },
            },
            {
                "message": "如果这家太远，就回到上面推荐的第一家，告诉我为什么适合我。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "plan_route"],
                    "answer_contains_any": ["第一", "推荐", "适合", "餐厅"],
                },
            },
        ],
    },
    {
        "id": "travel_chengdu_three_day_long",
        "turns": [
            {
                "message": (
                    "目的地：成都\n"
                    "出行时间：2026-06-10\n"
                    "出行天数：三天 2 晚\n"
                    "出行人数：1 人\n"
                    "我想去：宽窄巷子、武侯祠、杜甫草堂、成都大熊猫繁育研究基地、太古里、人民公园。\n"
                    "偏好：别太赶，想吃火锅和小吃。请先输出候选行程，等我确认后再继续。"
                ),
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_include": ["travel_search_poi"],
                    "max_tool_calls": 8,
                    "trip_meta": {"destination": "成都", "days": 3},
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "确认这些候选地点，请继续生成最终每日行程。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed"],
                    "business_state_in": ["itinerary_generated", "candidates_ready"],
                    "min_itinerary_days": 3,
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "第二天不要太累，把火锅安排在晚上，白天景点少一点。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "answer_contains_any": ["火锅", "第二天", "Day 2", "晚上"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "确认这版行程，请生成高德地图二维码并保存计划。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "failed"],
                    "business_state_in": ["map_generated", "itinerary_generated"],
                    "tool_calls_include_any": ["travel_create_personal_map"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "如果地图生成好了，总结一下三天每天上午和晚上的重点。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "answer_contains_any": ["Day 1", "Day 2", "Day 3", "第一天", "第二天", "第三天", "晚上"],
                    "no_prompt_artifact_pois": True,
                },
            },
        ],
    },
    {
        "id": "travel_revision_city_swap_resume_map_long",
        "turns": [
            {
                "message": "帮我做苏州 2 天旅行计划：拙政园、平江路、苏州博物馆、七里山塘。节奏慢一点。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_include": ["travel_search_poi"],
                    "max_tool_calls": 8,
                    "trip_meta": {"destination": "苏州", "days": 2},
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "临时改成杭州 1 天，不去拙政园，只保留西湖和灵隐寺，别太赶。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_include": ["travel_search_poi"],
                    "max_tool_calls": 8,
                    "trip_meta": {"destination": "杭州", "days": 1},
                    "candidate_expected_any": ["西湖", "灵隐寺"],
                    "candidate_not_contains_any": ["拙政园", "平江路", "苏州博物馆", "七里山塘"],
                    "itinerary_not_contains_any": ["拙政园", "平江路", "苏州博物馆", "七里山塘"],
                    "excluded_contains_any": ["拙政园"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "先别管旅行，陪我吐槽一句，改行程好烦。",
                "scene": "travel_planner",
                "expect": {
                    "worker_in": ["general_chat", "travel_planner"],
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_exclude": ["travel_search_poi", "travel_create_personal_map", "search_restaurants", "food_decision"],
                    "answer_contains_any": ["烦", "改行程", "理解", "休息"],
                },
            },
            {
                "message": "回到刚才杭州那版，确认并生成一天每日安排。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed"],
                    "business_state_in": ["itinerary_generated", "candidates_ready"],
                    "trip_meta": {"destination": "杭州", "days": 1},
                    "min_itinerary_days": 1,
                    "candidate_not_contains_any": ["拙政园", "平江路", "苏州博物馆", "七里山塘"],
                    "itinerary_not_contains_any": ["拙政园", "平江路", "苏州博物馆", "七里山塘"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "再给我生成高德地图二维码，地图标题要是杭州一日轻松游。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "failed"],
                    "business_state_in": ["map_generated", "itinerary_generated"],
                    "tool_calls_include_any": ["travel_create_personal_map"],
                    "trip_meta": {"destination": "杭州", "days": 1},
                    "candidate_not_contains_any": ["拙政园", "平江路", "苏州博物馆", "七里山塘"],
                    "itinerary_not_contains_any": ["拙政园", "平江路", "苏州博物馆", "七里山塘"],
                    "no_prompt_artifact_pois": True,
                },
            },
        ],
    },
    {
        "id": "home_chef_constraints_long",
        "turns": [
            {
                "message": "我冰箱里有鸡蛋、番茄、青椒和一点米饭，20 分钟内能做什么？",
                "scene": "home_chef",
                "expect": {
                    "worker": "home_chef",
                    "status_in": ["completed"],
                    "tool_calls_include_any": ["get_fridge_items", "rag_search_recipes", "search_recipes"],
                    "tool_calls_exclude": ["search_restaurants", "plan_route", "memory_search"],
                    "answer_contains_any": ["鸡蛋", "番茄", "20", "分钟"],
                },
            },
            {
                "message": "不要辣，也不要太油，给我更具体的步骤。",
                "scene": "home_chef",
                "expect": {
                    "worker": "home_chef",
                    "status_in": ["completed"],
                    "tool_calls_exclude": ["search_restaurants", "plan_route", "memory_search"],
                    "answer_contains_any": ["不要辣", "不辣", "少油", "步骤"],
                },
            },
            {
                "message": "我只有一个锅，不想洗太多东西，步骤再压缩一下。",
                "scene": "home_chef",
                "expect": {
                    "worker": "home_chef",
                    "status_in": ["completed"],
                    "tool_calls_exclude": ["search_restaurants", "plan_route", "memory_search"],
                    "answer_contains_any": ["一个锅", "少洗", "步骤", "锅"],
                },
            },
            {
                "message": "如果米饭是昨天剩的，有没有食品安全提醒？",
                "scene": "home_chef",
                "expect": {
                    "worker": "home_chef",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_exclude": ["search_restaurants", "plan_route", "memory_search"],
                    "answer_contains_any": ["剩饭", "加热", "安全", "冷藏", "隔夜"],
                },
            },
            {
                "message": "最后按 20 分钟时间线列出来。",
                "scene": "home_chef",
                "expect": {
                    "worker": "home_chef",
                    "status_in": ["completed"],
                    "tool_calls_exclude": ["search_restaurants", "plan_route", "memory_search"],
                    "answer_contains_any": ["20", "分钟", "时间线"],
                },
            },
        ],
    },
    {
        "id": "travel_food_route_cross_context_long",
        "turns": [
            {
                "message": "帮我做一个杭州 2 天轻松旅行计划：西湖、灵隐寺、河坊街，晚上想吃本地菜。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_include": ["travel_search_poi"],
                    "trip_meta": {"destination": "杭州", "days": 2},
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "确认候选地点，生成两天每日行程。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed"],
                    "business_state_in": ["itinerary_generated", "candidates_ready"],
                    "min_itinerary_days": 2,
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "第一天晚上在西湖附近找一家本帮菜或者杭帮菜，别太贵。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision"],
                    "min_restaurant_recommendations": 1,
                },
            },
            {
                "message": "就选第一家，安排进第一天晚餐。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision"],
                    "answer_contains_any": ["第一", "已选", "选定", "晚餐"],
                },
            },
            {
                "message": "从西湖到这家餐厅怎么走？",
                "scene": "travel_planner",
                "expect": {
                    "worker": "route_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "completed_tool_calls_include_any": ["plan_route"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision"],
                },
            },
        ],
    },
    {
        "id": "general_to_business_switch_long",
        "turns": [
            {
                "message": "我周末有点累，先陪我随便聊两句。",
                "scene": "chat",
                "expect": {"worker": "general_chat", "status_in": ["completed"], "no_tool_calls": True},
            },
            {
                "message": "不过我明天中午在上海人民广场附近，想吃清淡点，帮我找餐厅。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision"],
                    "min_restaurant_recommendations": 1,
                },
            },
            {
                "message": "第二家如果不合适，有没有更适合一个人吃的？",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "plan_route"],
                    "answer_contains_any": ["一个人", "第二", "适合", "餐厅"],
                },
            },
            {
                "message": "那就这家，帮我记住我今天想吃清淡。",
                "scene": "chat",
                "expect": {
                    "worker_in": ["food_advisor", "general_chat"],
                    "status_in": ["completed"],
                    "tool_calls_exclude": ["plan_route"],
                    "answer_contains_any": ["清淡", "记住", "已"],
                },
            },
            {
                "message": "现在回到刚才那家餐厅，给我一句简短推荐理由。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "plan_route"],
                    "answer_contains_any": ["清淡", "餐厅", "推荐"],
                },
            },
        ],
    },
    {
        "id": "travel_replan_food_route_negation_long",
        "turns": [
            {
                "message": "帮我做北京 2 天轻松旅行计划：故宫、景山、南锣鼓巷、雍和宫，第二晚想吃烤鸭。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_include": ["travel_search_poi"],
                    "trip_meta": {"destination": "北京", "days": 2},
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "故宫不去了，改成国家博物馆，而且不要太早起。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "answer_contains_any": ["国家博物馆", "不早起", "不要太早"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "确认这版，生成两天每日行程。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed"],
                    "business_state_in": ["itinerary_generated", "candidates_ready"],
                    "min_itinerary_days": 2,
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "第一天晚上在天安门附近找烤鸭店，预算 150 以内，别太游客化。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "travel_search_poi"],
                    "min_restaurant_recommendations": 1,
                },
            },
            {
                "message": "就第一家吧，然后从天安门到第一家怎么走？",
                "scene": "travel_planner",
                "expect": {
                    "worker": "route_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "completed_tool_calls_include_any": ["plan_route"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "search_restaurants", "travel_search_poi"],
                },
            },
        ],
    },
    {
        "id": "food_to_home_mind_change_long",
        "turns": [
            {
                "message": "今晚在深圳科技园附近想吃清淡点，一个人，人均 60 左右，找几家餐厅。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision"],
                    "min_restaurant_recommendations": 1,
                },
            },
            {
                "message": "第二家看起来一般，先别选，换一家更适合一个人吃的。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "plan_route"],
                    "answer_contains_any": ["一个人", "先别选", "避开", "换"],
                },
            },
            {
                "message": "算了不出门了，我家里有豆腐、青菜和鸡蛋，15 分钟内做饭。",
                "scene": "chat",
                "expect": {
                    "worker": "home_chef",
                    "status_in": ["completed"],
                    "tool_calls_include_any": ["get_fridge_items", "rag_search_recipes", "search_recipes"],
                    "tool_calls_exclude": ["search_restaurants", "plan_route", "memory_search"],
                    "answer_contains_any": ["豆腐", "青菜", "鸡蛋", "15"],
                },
            },
            {
                "message": "不要辣，蛋白质要够，步骤具体一点。",
                "scene": "chat",
                "expect": {
                    "worker": "home_chef",
                    "status_in": ["completed"],
                    "tool_calls_exclude": ["search_restaurants", "plan_route", "memory_search"],
                    "answer_contains_any": ["不辣", "蛋白质", "步骤"],
                },
            },
            {
                "message": "刚才那个餐厅先不用了，最后给我在家做饭的补充采购清单。",
                "scene": "chat",
                "expect": {
                    "worker": "home_chef",
                    "status_in": ["completed"],
                    "tool_calls_exclude": ["search_restaurants", "plan_route", "memory_search"],
                    "answer_contains_any": ["采购", "补充", "在家", "清单"],
                },
            },
        ],
    },
    {
        "id": "travel_multi_person_budget_change_long",
        "turns": [
            {
                "message": (
                    "帮我做广州 3 天旅行计划：陈家祠、沙面、永庆坊、广东省博物馆、北京路。"
                    "两个人，一个不吃辣，预算每天人均 300，住体育西附近。"
                ),
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_include": ["travel_search_poi"],
                    "trip_meta": {"destination": "广州", "days": 3},
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "改成 2 天，不去北京路，同行的人膝盖不好，少走路，晚餐想吃粤菜。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "answer_contains_any": ["2 天", "两天", "少走", "膝盖", "粤菜"],
                    "candidate_not_contains_any": ["北京路"],
                    "excluded_contains_any": ["北京路"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "确认这版行程，生成两天每日安排。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed"],
                    "business_state_in": ["itinerary_generated", "candidates_ready"],
                    "min_itinerary_days": 2,
                    "candidate_not_contains_any": ["北京路"],
                    "itinerary_not_contains_any": ["北京路"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "第二天晚上找离体育西近一点的粤菜，预算 100 以内，不要辣。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "travel_search_poi"],
                    "min_restaurant_recommendations": 1,
                },
            },
            {
                "message": "选第二家，但如果它不适合一个人少走路，就换第一家，并给我一句路线提示。",
                "scene": "travel_planner",
                "expect": {
                    "worker_in": ["food_advisor", "route_planner"],
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "travel_search_poi"],
                    "answer_contains_any": ["第一", "第二", "少走", "路线", "餐厅"],
                },
            },
        ],
    },
    {
        "id": "delayed_restaurant_route_after_context_switch_long",
        "turns": [
            {
                "message": "我在南京新街口附近，今晚想吃鸭血粉丝或者清淡小馆，人均 70，找两三家。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision"],
                    "min_restaurant_recommendations": 1,
                },
            },
            {
                "message": "先选第一家，理由短一点。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "plan_route"],
                    "answer_contains_any": ["第一", "已选", "餐厅"],
                },
            },
            {
                "message": "先别管吃饭，陪我随便聊一句，今天有点累。",
                "scene": "chat",
                "expect": {
                    "worker": "general_chat",
                    "status_in": ["completed"],
                    "no_tool_calls": True,
                },
            },
            {
                "message": "明早如果在家吃，我有鸡蛋和青菜，10 分钟能做什么？",
                "scene": "chat",
                "expect": {
                    "worker": "home_chef",
                    "status_in": ["completed"],
                    "tool_calls_include_any": ["get_fridge_items", "rag_search_recipes", "search_recipes"],
                    "tool_calls_exclude": ["search_restaurants", "plan_route", "memory_search"],
                    "answer_contains_any": ["鸡蛋", "青菜", "10", "分钟"],
                },
            },
            {
                "message": "还是回到刚才选的那家餐厅，从新街口过去怎么走？",
                "scene": "chat",
                "expect": {
                    "worker": "route_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "completed_tool_calls_include_any": ["plan_route"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "search_restaurants", "rag_search_recipes"],
                    "answer_contains_any": ["路线", "怎么走", "步行", "距离", "出发", "分钟"],
                },
            },
        ],
    },
    {
        "id": "route_clarification_recovery_long",
        "turns": [
            {
                "message": "我先问个简单的，今天状态还行吗？随便回一句就好。",
                "scene": "chat",
                "expect": {"worker": "general_chat", "status_in": ["completed"], "no_tool_calls": True},
            },
            {
                "message": "那怎么走？",
                "scene": "chat",
                "expect": {
                    "worker": "route_planner",
                    "status_in": ["needs_clarification"],
                    "no_tool_calls": True,
                    "active_skills_include": ["route_planner"],
                    "active_skills_exclude": ["food_assistant", "restaurant_finder", "travel_plan_new"],
                    "answer_contains_any": ["去哪", "目的地", "餐厅", "景点"],
                },
            },
            {
                "message": "我还没选地方，先找上海外滩附近清淡点的餐厅，人均 120 以内。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "plan_route"],
                    "active_skills_include": ["food_assistant"],
                    "active_skills_exclude": ["route_planner", "travel_plan_new"],
                    "min_restaurant_recommendations": 1,
                },
            },
            {
                "message": "第二家从南京东路地铁站过去怎么走？",
                "scene": "chat",
                "expect": {
                    "worker": "route_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "completed_tool_calls_include_any": ["plan_route"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "search_restaurants"],
                    "active_skills_include": ["route_planner"],
                    "active_skills_exclude": ["food_assistant", "restaurant_finder", "travel_plan_new"],
                    "answer_contains_any": ["路线", "步行", "地铁站", "距离", "分钟"],
                },
            },
            {
                "message": "如果第二家不够清淡，就回第一家，给我一句推荐理由。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "plan_route", "travel_search_poi"],
                    "active_skills_include": ["food_assistant"],
                    "active_skills_exclude": ["route_planner", "travel_plan_new"],
                    "answer_contains_any": ["第一", "清淡", "推荐", "餐厅"],
                },
            },
        ],
    },
    {
        "id": "travel_city_swap_chitchat_resume_long",
        "turns": [
            {
                "message": "帮我做苏州 2 天旅行计划：拙政园、平江路、苏州博物馆、七里山塘。节奏慢一点。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_include": ["travel_search_poi"],
                    "trip_meta": {"destination": "苏州", "days": 2},
                    "active_skills_include": ["travel_plan_new"],
                    "active_skills_exclude": ["food_assistant", "route_planner"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "临时改成杭州 1 天，不去拙政园，只保留西湖和灵隐寺，别太赶。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "trip_meta": {"destination": "杭州", "days": 1},
                    "candidate_not_contains_any": ["拙政园"],
                    "excluded_contains_any": ["拙政园"],
                    "answer_contains_any": ["杭州", "1 天", "一天", "西湖", "灵隐寺"],
                    "active_skills_include": ["travel_plan_new"],
                    "active_skills_exclude": ["food_assistant", "route_planner"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "先别管旅行，陪我吐槽一句，改行程好烦。",
                "scene": "chat",
                "expect": {
                    "worker": "general_chat",
                    "status_in": ["completed"],
                    "no_tool_calls": True,
                    "active_skills_exclude": ["travel_plan_new", "food_assistant", "route_planner"],
                },
            },
            {
                "message": "回到刚才杭州那版，确认并生成一天每日安排。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed"],
                    "business_state_in": ["itinerary_generated", "candidates_ready"],
                    "min_itinerary_days": 1,
                    "itinerary_not_contains_any": ["拙政园"],
                    "active_skills_include": ["travel_plan_new"],
                    "active_skills_exclude": ["food_assistant", "route_planner"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "再给我生成高德地图二维码，地图标题要是杭州一日轻松游。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "failed"],
                    "business_state_in": ["map_generated", "itinerary_generated"],
                    "tool_calls_include_any": ["travel_create_personal_map"],
                    "active_skills_include": ["travel_plan_new"],
                    "active_skills_exclude": ["food_assistant", "route_planner"],
                    "answer_contains_any": ["杭州", "地图", "二维码", "一日"],
                    "no_prompt_artifact_pois": True,
                },
            },
        ],
    },
    {
        "id": "group_food_preference_undo_route_long",
        "turns": [
            {
                "message": "今晚三个人在广州天河吃饭：一个不吃辣，一个偏素，我想吃粤菜，人均 100 左右，帮我找餐厅。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "plan_route"],
                    "active_skills_include": ["food_assistant"],
                    "active_skills_exclude": ["route_planner", "travel_plan_new"],
                    "min_restaurant_recommendations": 1,
                },
            },
            {
                "message": "第二家如果有辣菜就不要，换成更适合偏素朋友的。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "plan_route", "travel_search_poi"],
                    "active_skills_include": ["food_assistant"],
                    "active_skills_exclude": ["route_planner", "travel_plan_new"],
                    "answer_contains_any": ["偏素", "不辣", "适合", "换", "餐厅"],
                },
            },
            {
                "message": "等等，偏素朋友临时不来了，恢复普通粤菜，但仍然不要辣。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "plan_route", "travel_search_poi"],
                    "active_skills_include": ["food_assistant"],
                    "active_skills_exclude": ["route_planner", "travel_plan_new"],
                    "answer_contains_any": ["粤菜", "不辣", "餐厅", "推荐"],
                },
            },
            {
                "message": "就选第一家，从体育西路地铁站过去怎么走？",
                "scene": "chat",
                "expect": {
                    "worker": "route_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "completed_tool_calls_include_any": ["plan_route"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "search_restaurants", "travel_search_poi"],
                    "active_skills_include": ["route_planner"],
                    "active_skills_exclude": ["food_assistant", "restaurant_finder", "travel_plan_new"],
                    "answer_contains_any": ["路线", "体育西", "地铁", "步行", "分钟"],
                },
            },
            {
                "message": "如果第一家太远，别选第一家，回到刚才餐厅推荐里更近的那家，给一句理由。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "plan_route", "travel_search_poi"],
                    "active_skills_include": ["food_assistant"],
                    "active_skills_exclude": ["route_planner", "travel_plan_new"],
                    "answer_contains_any": ["第一", "更近", "推荐", "餐厅", "理由"],
                },
            },
        ],
    },
    {
        "id": "travel_exclusion_correction_food_long",
        "turns": [
            {
                "message": "帮我做厦门 2 天旅行计划：鼓浪屿、南普陀寺、沙坡尾、曾厝垵，想轻松一点。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_include": ["travel_search_poi"],
                    "trip_meta": {"destination": "厦门", "days": 2},
                    "active_skills_include": ["travel_plan_new"],
                    "active_skills_exclude": ["food_assistant", "route_planner"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "沙坡尾不去了，改成厦门植物园。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "candidate_not_contains_any": ["沙坡尾"],
                    "excluded_contains_any": ["沙坡尾"],
                    "answer_contains_any": ["植物园", "沙坡尾"],
                    "active_skills_include": ["travel_plan_new"],
                    "active_skills_exclude": ["food_assistant", "route_planner"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "我刚才说错了，不去的是曾厝垵，沙坡尾保留，植物园也保留。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "candidate_not_contains_any": ["曾厝垵"],
                    "excluded_contains_any": ["曾厝垵"],
                    "answer_contains_any": ["沙坡尾", "植物园", "曾厝垵"],
                    "active_skills_include": ["travel_plan_new"],
                    "active_skills_exclude": ["food_assistant", "route_planner"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "确认这版，生成两天每日安排。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed"],
                    "business_state_in": ["itinerary_generated", "candidates_ready"],
                    "min_itinerary_days": 2,
                    "itinerary_not_contains_any": ["曾厝垵"],
                    "active_skills_include": ["travel_plan_new"],
                    "active_skills_exclude": ["food_assistant", "route_planner"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "第二天晚上在沙坡尾附近找闽南菜，人均 100，不要太油。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "travel_search_poi"],
                    "active_skills_include": ["food_assistant"],
                    "active_skills_exclude": ["route_planner", "travel_plan_new"],
                    "min_restaurant_recommendations": 1,
                },
            },
        ],
    },
    {
        "id": "travel_hotel_shift_food_route_long",
        "turns": [
            {
                "message": "帮我做重庆 3 天旅行计划：解放碑、洪崖洞、磁器口、李子坝、鹅岭二厂。住观音桥，别太赶。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_include": ["travel_search_poi"],
                    "trip_meta": {"destination": "重庆", "days": 3},
                    "active_skills_include": ["travel_plan_new"],
                    "active_skills_exclude": ["food_assistant", "route_planner"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "酒店改到解放碑附近，洪崖洞不去了，想少排队，多安排室内和轻松一点的点。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "candidate_not_contains_any": ["洪崖洞"],
                    "excluded_contains_any": ["洪崖洞"],
                    "answer_contains_any": ["解放碑", "洪崖洞", "室内", "轻松"],
                    "active_skills_include": ["travel_plan_new"],
                    "active_skills_exclude": ["food_assistant", "route_planner"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "确认这版，生成三天每日安排。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "travel_planner",
                    "status_in": ["completed"],
                    "business_state_in": ["itinerary_generated", "candidates_ready"],
                    "min_itinerary_days": 3,
                    "itinerary_not_contains_any": ["洪崖洞"],
                    "active_skills_include": ["travel_plan_new"],
                    "active_skills_exclude": ["food_assistant", "route_planner"],
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "第一天晚上在解放碑附近找火锅，但同行有人不能吃太辣，人均 120。",
                "scene": "travel_planner",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "travel_search_poi"],
                    "active_skills_include": ["food_assistant"],
                    "active_skills_exclude": ["route_planner", "travel_plan_new"],
                    "min_restaurant_recommendations": 1,
                },
            },
            {
                "message": "就选更适合不能吃辣的那家，从解放碑步行过去怎么走？",
                "scene": "travel_planner",
                "expect": {
                    "worker": "route_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "completed_tool_calls_include_any": ["plan_route"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "search_restaurants", "travel_search_poi"],
                    "active_skills_include": ["route_planner"],
                    "active_skills_exclude": ["food_assistant", "restaurant_finder", "travel_plan_new"],
                    "answer_contains_any": ["路线", "解放碑", "步行", "分钟", "距离"],
                },
            },
        ],
    },
    {
        "id": "food_allergy_budget_context_route_long",
        "turns": [
            {
                "message": "我在北京三里屯，今晚两个人吃饭，一个海鲜过敏，预算人均 150，想吃不太油的。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "plan_route"],
                    "active_skills_include": ["food_assistant"],
                    "active_skills_exclude": ["route_planner", "travel_plan_new"],
                    "min_restaurant_recommendations": 1,
                },
            },
            {
                "message": "第二家如果有海鲜就不要，换一家更安全的，仍然别太油。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "plan_route", "travel_search_poi"],
                    "active_skills_include": ["food_assistant"],
                    "active_skills_exclude": ["route_planner", "travel_plan_new"],
                    "answer_contains_any": ["海鲜", "过敏", "安全", "不油", "餐厅"],
                },
            },
            {
                "message": "先陪我随便聊一句，今天不想做复杂决定。",
                "scene": "chat",
                "expect": {
                    "worker": "general_chat",
                    "status_in": ["completed"],
                    "no_tool_calls": True,
                    "active_skills_exclude": ["food_assistant", "route_planner", "travel_plan_new"],
                },
            },
            {
                "message": "回到刚才吃饭，选第一家安全的，并提醒我过敏注意点。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "plan_route"],
                    "active_skills_include": ["food_assistant"],
                    "active_skills_exclude": ["route_planner", "travel_plan_new"],
                    "answer_contains_any": ["第一", "过敏", "海鲜", "安全", "餐厅"],
                },
            },
            {
                "message": "从三里屯太古里过去怎么走？",
                "scene": "chat",
                "expect": {
                    "worker": "route_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "completed_tool_calls_include_any": ["plan_route"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "search_restaurants", "travel_search_poi"],
                    "active_skills_include": ["route_planner"],
                    "active_skills_exclude": ["food_assistant", "restaurant_finder", "travel_plan_new"],
                    "answer_contains_any": ["路线", "三里屯", "太古里", "分钟", "距离"],
                },
            },
        ],
    },
    {
        "id": "home_outside_food_mind_switch_long",
        "turns": [
            {
                "message": "我家里有牛肉、洋葱、土豆和米饭，30 分钟能做什么？不要太辣。",
                "scene": "chat",
                "expect": {
                    "worker": "home_chef",
                    "status_in": ["completed"],
                    "tool_calls_include_any": ["get_fridge_items", "rag_search_recipes", "search_recipes"],
                    "tool_calls_exclude": ["search_restaurants", "plan_route", "memory_search"],
                    "active_skills_include": ["home_chef"],
                    "active_skills_exclude": ["food_assistant", "route_planner", "travel_plan_new"],
                    "answer_contains_any": ["牛肉", "洋葱", "土豆", "30", "不辣"],
                },
            },
            {
                "message": "但是我突然不想做饭了，在成都春熙路附近找不辣的牛肉类餐厅，人均 90。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "rag_search_recipes", "plan_route"],
                    "active_skills_include": ["food_assistant"],
                    "active_skills_exclude": ["home_chef", "route_planner", "travel_plan_new"],
                    "min_restaurant_recommendations": 1,
                },
            },
            {
                "message": "算了又想在家吃，把刚才家里的食材继续用上，做得清淡一点。",
                "scene": "chat",
                "expect": {
                    "worker": "home_chef",
                    "status_in": ["completed"],
                    "tool_calls_exclude": ["search_restaurants", "plan_route", "memory_search"],
                    "active_skills_include": ["home_chef"],
                    "active_skills_exclude": ["food_assistant", "route_planner", "travel_plan_new"],
                    "answer_contains_any": ["牛肉", "洋葱", "土豆", "清淡", "在家"],
                },
            },
            {
                "message": "如果做饭失败，再回春熙路选第一家餐厅，但先不要规划路线。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "plan_route", "travel_search_poi"],
                    "active_skills_include": ["food_assistant"],
                    "active_skills_exclude": ["route_planner", "travel_plan_new"],
                    "answer_contains_any": ["第一", "春熙路", "餐厅", "先不", "路线"],
                },
            },
            {
                "message": "最后给我两个方案对比：在家做和出去吃，各一句话。",
                "scene": "chat",
                "expect": {
                    "worker_in": ["home_chef", "food_advisor", "general_chat"],
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_exclude": ["plan_route", "travel_search_poi"],
                    "answer_contains_any": ["在家", "出去", "餐厅", "做饭"],
                },
            },
        ],
    },
    {
        "id": "time_boxed_transit_food_route_long",
        "turns": [
            {
                "message": "我明天下午在上海虹桥火车站转车，有 4 小时空档，想轻松吃个饭再逛一小会儿，别太赶。",
                "scene": "chat",
                "expect": {
                    "worker_in": ["travel_planner", "food_advisor"],
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision"],
                    "answer_contains_any": ["虹桥", "4 小时", "四小时", "吃饭", "轻松"],
                },
            },
            {
                "message": "不要商场，想安静一点，而且我不能吃辣。",
                "scene": "chat",
                "expect": {
                    "worker_in": ["travel_planner", "food_advisor"],
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "plan_route"],
                    "answer_contains_any": ["不辣", "安静", "商场", "避开", "清淡"],
                },
            },
            {
                "message": "那先找餐厅，人均 80 左右，离虹桥火车站近一点。",
                "scene": "chat",
                "expect": {
                    "worker": "food_advisor",
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "travel_search_poi", "plan_route"],
                    "active_skills_include": ["food_assistant"],
                    "active_skills_exclude": ["travel_plan_new", "route_planner"],
                    "min_restaurant_recommendations": 1,
                },
            },
            {
                "message": "如果时间来不及，就只保留最近那家餐厅，别安排景点。",
                "scene": "chat",
                "expect": {
                    "worker_in": ["food_advisor", "travel_planner"],
                    "status_in": ["completed", "needs_clarification"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "plan_route"],
                    "answer_contains_any": ["最近", "餐厅", "不安排", "景点", "时间"],
                },
            },
            {
                "message": "从虹桥火车站到最终保留的那家怎么走？如果超过 30 分钟就提醒我别去了。",
                "scene": "chat",
                "expect": {
                    "worker": "route_planner",
                    "status_in": ["completed", "needs_clarification"],
                    "completed_tool_calls_include_any": ["plan_route"],
                    "tool_calls_exclude": ["memory_search", "source_event_search", "food_decision", "search_restaurants", "travel_search_poi"],
                    "active_skills_include": ["route_planner"],
                    "active_skills_exclude": ["food_assistant", "restaurant_finder", "travel_plan_new"],
                    "answer_contains_any": ["路线", "虹桥", "30", "分钟", "提醒"],
                },
            },
        ],
    },
]


QUALITY_DIMENSIONS = (
    "contract_validity",
    "route_accuracy",
    "worker_completion",
    "tool_policy",
    "business_payload",
    "answer_quality",
    "provider_availability",
)


def final_event_from_sse(text: str) -> dict[str, Any] | None:
    event = None
    final_payload: dict[str, Any] | None = None
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and event == "final":
            data = line.split(":", 1)[1].strip()
            try:
                final_payload = json.loads(data)
            except json.JSONDecodeError:
                pass
    return final_payload


def fetch_session_message_counts(client: Any, session_id: str, *, timeout_seconds: float) -> dict[str, int]:
    response = client.get(
        f"/api/v1/chat/sessions/{session_id}/messages",
        params={"limit": 200, "offset": 0},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    body = response.json()
    data = body.get("data") if isinstance(body, dict) else {}
    messages = data.get("messages") if isinstance(data, dict) else []
    counts = {"total": 0, "user": 0, "assistant": 0, "tool": 0}
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        counts["total"] += 1
        if role in counts:
            counts[role] += 1
    return counts


def wait_for_turn_persistence(
    client: Any,
    session_id: str,
    before_counts: dict[str, int],
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        return {"ok": True, "skipped": True}
    deadline = time.monotonic() + timeout_seconds
    last_counts: dict[str, int] | None = None
    last_error: str | None = None
    while time.monotonic() <= deadline:
        try:
            last_counts = fetch_session_message_counts(client, session_id, timeout_seconds=min(10.0, timeout_seconds))
            last_error = None
            if last_counts.get("assistant", 0) > before_counts.get("assistant", 0):
                return {"ok": True, "before": before_counts, "after": last_counts}
        except Exception as exc:  # pragma: no cover - exercised by live probe failures.
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(max(0.05, poll_seconds))
    return {
        "ok": False,
        "before": before_counts,
        "after": last_counts,
        "error": last_error,
        "reason": "assistant_not_persisted_after_final",
    }


def run_scenario(
    client: Any,
    scenario: dict[str, Any],
    *,
    model_value: str | None,
    timeout_seconds: float,
    request_delay_seconds: float,
    persist_wait_seconds: float,
    persist_poll_seconds: float,
) -> dict[str, Any]:
    session_resp = client.post("/api/v1/chat/sessions", timeout=timeout_seconds)
    session_resp.raise_for_status()
    session_id = session_resp.json()["data"]["session_id"]
    turn_results = []
    for index, turn in enumerate(scenario.get("turns") or []):
        if index and request_delay_seconds > 0:
            time.sleep(request_delay_seconds)
        try:
            before_counts = fetch_session_message_counts(client, session_id, timeout_seconds=timeout_seconds)
        except Exception:
            before_counts = {"total": 0, "user": 0, "assistant": 0, "tool": 0}
        result = run_turn(
            client,
            session_id,
            {**turn, "id": f"{scenario.get('id')}:{index + 1}"},
            model_value=model_value,
            timeout_seconds=timeout_seconds,
        )
        persistence = wait_for_turn_persistence(
            client,
            session_id,
            before_counts,
            timeout_seconds=persist_wait_seconds,
            poll_seconds=persist_poll_seconds,
        )
        result["persistence"] = persistence
        if not persistence.get("ok") and not result.get("provider_unavailable"):
            evaluation = result.setdefault("evaluation", {"passed": True, "violations": []})
            violations = evaluation.setdefault("violations", [])
            violations.append("assistant_not_persisted_after_final")
            evaluation["passed"] = False
        turn_results.append(result)
        if result.get("provider_unavailable"):
            break
    violations = [
        f"{item.get('id')}:{violation}"
        for item in turn_results
        for violation in ((item.get("evaluation") or {}).get("violations") or [])
    ]
    provider_unavailable = next(
        (item.get("provider_unavailable") for item in turn_results if item.get("provider_unavailable")),
        None,
    )
    return {
        "id": scenario.get("id"),
        "session_id": session_id,
        "passed": not violations and not provider_unavailable,
        "violations": violations,
        "provider_unavailable": provider_unavailable,
        "turns": turn_results,
    }


def aggregate_quality_metrics(
    results: list[dict[str, Any]],
    *,
    total_planned: int | None = None,
    provider_unavailable: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scenario_count = len(results)
    turns = [
        turn
        for scenario in results
        for turn in (scenario.get("turns") or [])
        if isinstance(turn, dict)
    ]
    total_turns = len(turns)
    provider_unavailable = provider_unavailable or next((item.get("provider_unavailable") for item in results if item.get("provider_unavailable")), None)
    dimension_failed_turns: dict[str, set[str]] = {key: set() for key in QUALITY_DIMENSIONS}
    violation_counts: dict[str, int] = {}
    for turn in turns:
        turn_id = str(turn.get("id") or "")
        violations = ((turn.get("evaluation") or {}).get("violations") or [])
        for violation in violations:
            if not isinstance(violation, str):
                continue
            violation_counts[violation] = violation_counts.get(violation, 0) + 1
            dimension_failed_turns.setdefault(_violation_dimension(violation), set()).add(turn_id)

    provider_blocked = isinstance(provider_unavailable, dict)
    if provider_blocked and not total_turns:
        dimension_failed_turns["provider_availability"].add("provider_preflight")
        code = provider_unavailable.get("http_status") or provider_unavailable.get("code") or "unknown"
        violation_counts[f"provider_unavailable:{code}"] = 1
    denominator = total_turns or 0
    dimension_scores = {
        key: (None if provider_blocked else _score_from_failed_turns(len(failed_turns), denominator))
        for key, failed_turns in dimension_failed_turns.items()
    }
    agent_failed_turns = [
        turn
        for turn in turns
        if turn.get("status") == "failed" or (turn.get("failure_class") and not turn.get("provider_unavailable"))
    ]
    return {
        "total_planned_scenarios": total_planned if total_planned is not None else scenario_count,
        "attempted_scenarios": scenario_count,
        "attempted_turns": total_turns,
        "scenario_pass_rate": None if provider_blocked else ((sum(1 for item in results if item.get("passed")) / scenario_count) if scenario_count else None),
        "turn_pass_rate": None if provider_blocked else ((sum(1 for turn in turns if (turn.get("evaluation") or {}).get("passed")) / total_turns) if total_turns else None),
        "dimension_scores": dimension_scores,
        "dimension_failed_turn_counts": {key: len(value) for key, value in dimension_failed_turns.items()},
        "violation_counts": dict(sorted(violation_counts.items(), key=lambda item: item[1], reverse=True)),
        "agent_failed_turn_count": len(agent_failed_turns),
        "user_visible_fallback_rate": None if provider_blocked else ((len(agent_failed_turns) / total_turns) if total_turns else None),
        "provider_blocked": provider_blocked,
    }


def _score_from_failed_turns(failed_turns: int, total_turns: int) -> float | None:
    if total_turns <= 0:
        return None
    return round(max(0.0, 1.0 - (failed_turns / total_turns)), 4)


def _violation_dimension(violation: str) -> str:
    if violation.startswith(("provider_unavailable", "http_error")):
        return "provider_availability"
    if violation in {
        "missing_trace_id",
        "missing_agent_result",
        "failed_missing_failure_class",
        "assistant_not_persisted_after_final",
    } or violation.startswith("non_failed_failure_class"):
        return "contract_validity"
    if violation.startswith("worker:"):
        return "route_accuracy"
    if violation.startswith("status:"):
        return "worker_completion"
    if any(
        violation.startswith(prefix)
        for prefix in (
            "unexpected_tool_calls",
            "missing_tool_call",
            "missing_any_tool_call",
            "completed_missing_any_tool_call",
            "too_many_tool_calls",
            "missing_active_skill",
            "unexpected_active_skills",
        )
    ):
        return "tool_policy"
    if any(
        violation.startswith(prefix)
        for prefix in (
            "restaurant_recommendations",
            "business_state",
            "trip_meta",
            "itinerary_days",
            "prompt_artifact_pois",
            "candidate_missing_any",
            "candidate_unexpected_any",
            "itinerary_unexpected_any",
            "excluded_missing_any",
        )
    ):
        return "business_payload"
    if violation.startswith(("answer_missing_any", "answer_unexpected_any")):
        return "answer_quality"
    return "answer_quality"


def run_turn(
    client: Any,
    session_id: str,
    turn: dict[str, Any],
    *,
    model_value: str | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    import httpx

    response = None
    try:
        for attempt in range(5):
            response = client.post(
                f"/api/v1/chat/sessions/{session_id}/stream",
                json={
                    key: value
                    for key, value in {
                        "message": turn.get("message"),
                        "scene": turn.get("scene"),
                        "model": model_value,
                        "client_context_overrides": turn.get("client_context_overrides") or turn.get("context_overrides"),
                    }.items()
                    if value is not None
                },
                headers={"accept": "text/event-stream"},
                timeout=timeout_seconds,
            )
            if response.status_code not in {429, 503}:
                response.raise_for_status()
                break
            if attempt == 4:
                response.raise_for_status()
            time.sleep(min(45.0, 4.0 * (attempt + 1)))
    except httpx.HTTPError as exc:
        result = {
            "id": turn.get("id"),
            "message": turn.get("message"),
            "http_error": str(exc),
            "status": "failed",
            "worker": None,
            "intent": None,
            "tool_calls": [],
            "active_tools": [],
            "answer": {},
            "agent_result": {},
            "failure_class": "http_error",
        }
        result["evaluation"] = {"passed": False, "violations": [f"http_error:{type(exc).__name__}"]}
        return result

    if response is None:
        raise RuntimeError("unreachable: missing probe response")
    final_payload = final_event_from_sse(response.text) or {}
    answer = final_payload.get("answer") if isinstance(final_payload.get("answer"), dict) else {}
    agent_result = final_payload.get("agent_result") if isinstance(final_payload.get("agent_result"), dict) else {}
    diagnostics = agent_result.get("diagnostics") if isinstance(agent_result.get("diagnostics"), dict) else {}
    route = diagnostics.get("route") if isinstance(diagnostics.get("route"), dict) else {}
    result = {
        "id": turn.get("id"),
        "message": turn.get("message"),
        "trace_id": final_payload.get("trace_id"),
        "status": agent_result.get("status"),
        "worker": route.get("worker") or agent_result.get("worker"),
        "intent": route.get("intent"),
        "failure_class": final_payload.get("failure_class") or agent_result.get("failure_class"),
        "tool_calls": _tool_call_names(diagnostics.get("tools")),
        "active_tools": _string_list(diagnostics.get("active_tools")),
        "active_skills": _active_skill_ids(diagnostics.get("active_skills")),
        "answer": answer,
        "agent_result": agent_result,
        "text": _visible_text(answer),
    }
    provider_unavailable = _provider_unavailable_issue_from_result(result)
    if provider_unavailable:
        result["provider_unavailable"] = provider_unavailable
    result["evaluation"] = evaluate_turn(turn, result)
    return result


def evaluate_turn(turn: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    expect = turn.get("expect") if isinstance(turn.get("expect"), dict) else {}
    violations: list[str] = []
    provider_unavailable = result.get("provider_unavailable")
    if isinstance(provider_unavailable, dict):
        code = provider_unavailable.get("http_status") or provider_unavailable.get("code") or "unknown"
        return {"passed": False, "violations": [f"provider_unavailable:{code}"]}
    if not result.get("trace_id"):
        violations.append("missing_trace_id")
    if not isinstance(result.get("agent_result"), dict) or not result.get("agent_result"):
        violations.append("missing_agent_result")
    if result.get("failure_class") and result.get("status") != "failed":
        violations.append(f"non_failed_failure_class:{result.get('failure_class')}")
    if result.get("status") == "failed" and not result.get("failure_class"):
        violations.append("failed_missing_failure_class")

    expected_worker = expect.get("worker")
    if isinstance(expected_worker, str) and result.get("worker") != expected_worker:
        violations.append(f"worker:{result.get('worker')}!=expected:{expected_worker}")
    worker_in = expect.get("worker_in")
    if isinstance(worker_in, list) and result.get("worker") not in set(worker_in):
        violations.append(f"worker:{result.get('worker')} not in {worker_in}")
    status_in = expect.get("status_in")
    if isinstance(status_in, list) and result.get("status") not in set(status_in):
        violations.append(f"status:{result.get('status')} not in {status_in}")
    if expect.get("no_tool_calls") is True and result.get("tool_calls"):
        violations.append(f"unexpected_tool_calls:{','.join(result.get('tool_calls') or [])}")

    tool_calls = set(result.get("tool_calls") or [])
    for name in expect.get("tool_calls_include") or []:
        if isinstance(name, str) and name not in tool_calls:
            violations.append(f"missing_tool_call:{name}")
    include_any = [item for item in expect.get("tool_calls_include_any") or [] if isinstance(item, str)]
    if include_any and not tool_calls.intersection(include_any):
        violations.append(f"missing_any_tool_call:{','.join(include_any)}")
    completed_include_any = [item for item in expect.get("completed_tool_calls_include_any") or [] if isinstance(item, str)]
    if result.get("status") == "completed" and completed_include_any and not tool_calls.intersection(completed_include_any):
        violations.append(f"completed_missing_any_tool_call:{','.join(completed_include_any)}")
    excluded = sorted({name for name in expect.get("tool_calls_exclude") or [] if isinstance(name, str) and name in tool_calls})
    if excluded:
        violations.append(f"unexpected_tool_calls:{','.join(excluded)}")
    max_tool_calls = expect.get("max_tool_calls")
    if isinstance(max_tool_calls, int) and len(result.get("tool_calls") or []) > max_tool_calls:
        violations.append(f"too_many_tool_calls:{len(result.get('tool_calls') or [])}>{max_tool_calls}")

    active_skills = set(result.get("active_skills") or [])
    for skill_id in expect.get("active_skills_include") or []:
        if isinstance(skill_id, str) and skill_id not in active_skills:
            violations.append(f"missing_active_skill:{skill_id}")
    excluded_skills = sorted({skill_id for skill_id in expect.get("active_skills_exclude") or [] if isinstance(skill_id, str) and skill_id in active_skills})
    if excluded_skills:
        violations.append(f"unexpected_active_skills:{','.join(excluded_skills)}")

    min_restaurants = expect.get("min_restaurant_recommendations")
    if isinstance(min_restaurants, int) and _restaurant_recommendation_count(result) < min_restaurants:
        violations.append(f"restaurant_recommendations:{_restaurant_recommendation_count(result)}<{min_restaurants}")

    business_state_in = expect.get("business_state_in")
    state = _final(result).get("state")
    if isinstance(business_state_in, list) and state not in set(business_state_in):
        violations.append(f"business_state:{state} not in {business_state_in}")

    trip_meta_expect = expect.get("trip_meta")
    if isinstance(trip_meta_expect, dict):
        trip_meta = _final(result).get("trip_meta") if isinstance(_final(result).get("trip_meta"), dict) else {}
        for key, value in trip_meta_expect.items():
            if trip_meta.get(key) != value:
                violations.append(f"trip_meta:{key}:{trip_meta.get(key)}!=expected:{value}")

    min_days = expect.get("min_itinerary_days")
    if isinstance(min_days, int):
        day_count = _itinerary_day_count(_final(result))
        if day_count and day_count < min_days:
            violations.append(f"itinerary_days:{day_count}<{min_days}")
        if not day_count:
            violations.append("itinerary_days:missing")

    if expect.get("no_prompt_artifact_pois") and _prompt_artifact_place_names(_final(result)):
        violations.append(f"prompt_artifact_pois:{','.join(_prompt_artifact_place_names(_final(result))[:3])}")

    candidate_expected_any = [item for item in expect.get("candidate_expected_any") or [] if isinstance(item, str) and item]
    if candidate_expected_any:
        names = _candidate_names(_final(result))
        missing = [item for item in candidate_expected_any if not any(item in name for name in names)]
        if missing:
            violations.append(f"candidate_missing_any:{','.join(missing)}")
    candidate_not_contains_any = [item for item in expect.get("candidate_not_contains_any") or [] if isinstance(item, str) and item]
    if candidate_not_contains_any:
        names = _candidate_names(_final(result))
        found = [item for item in candidate_not_contains_any if any(item in name for name in names)]
        if found:
            violations.append(f"candidate_unexpected_any:{','.join(found)}")
    itinerary_not_contains_any = [item for item in expect.get("itinerary_not_contains_any") or [] if isinstance(item, str) and item]
    if itinerary_not_contains_any:
        names = _itinerary_place_names(_final(result))
        found = [item for item in itinerary_not_contains_any if any(item in name for name in names)]
        if found:
            violations.append(f"itinerary_unexpected_any:{','.join(found)}")
    excluded_contains_any = [item for item in expect.get("excluded_contains_any") or [] if isinstance(item, str) and item]
    if excluded_contains_any:
        names = _excluded_place_names(_final(result))
        if not any(item in name for item in excluded_contains_any for name in names):
            violations.append(f"excluded_missing_any:{','.join(excluded_contains_any)}")

    contains_any = [item for item in expect.get("answer_contains_any") or [] if isinstance(item, str) and item]
    if contains_any:
        text = result.get("text") or json.dumps(_final(result), ensure_ascii=False)
        if not any(item in text for item in contains_any):
            violations.append(f"answer_missing_any:{','.join(contains_any)}")
    not_contains_any = [item for item in expect.get("answer_not_contains_any") or [] if isinstance(item, str) and item]
    if not_contains_any:
        text = result.get("text") or json.dumps(_final(result), ensure_ascii=False)
        found = [item for item in not_contains_any if item in text]
        if found:
            violations.append(f"answer_unexpected_any:{','.join(found)}")
    return {"passed": not violations, "violations": violations}


def _provider_unavailable_issue_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    agent_result = result.get("agent_result") if isinstance(result.get("agent_result"), dict) else {}
    final = agent_result.get("final") if isinstance(agent_result.get("final"), dict) else {}
    diagnostics = agent_result.get("diagnostics") if isinstance(agent_result.get("diagnostics"), dict) else {}
    issues = [
        diagnostics.get("provider_issue") if isinstance(diagnostics.get("provider_issue"), dict) else None,
        final.get("provider_issue") if isinstance(final.get("provider_issue"), dict) else None,
    ]
    text = result.get("text") or json.dumps(final, ensure_ascii=False)
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        http_status = issue.get("http_status")
        if http_status in {401, 402, 403}:
            return {
                "kind": "provider_unavailable",
                "http_status": http_status,
                "code": issue.get("code") or issue.get("provider_error_code") or "provider_auth_or_billing",
                "message": issue.get("user_message") or _safe_provider_message(text),
            }
    if isinstance(text, str) and any(token in text for token in ("Insufficient Balance", "Payment Required", "余额不足")):
        return {
            "kind": "provider_unavailable",
            "http_status": 402,
            "code": "provider_billing_unavailable",
            "message": _safe_provider_message(text),
        }
    return None


def preflight_provider(model_value: str | None, *, timeout_seconds: float) -> dict[str, Any] | None:
    config = _preflight_config_from_env(model_value)
    if not config:
        return None
    api_key = str(config.get("api_key") or "").strip()
    if not api_key:
        return {
            "kind": "provider_unavailable",
            "code": "provider_api_key_missing",
            "category": "provider_auth",
            "message": f"{config.get('provider')} API key is not set for dialogue probe preflight.",
            "action": "set_provider_api_key",
            "model_config": _safe_preflight_model_config(config),
        }

    import httpx

    base_url = str(config.get("base_url") or "").rstrip("/")
    url = f"{base_url}/chat/completions"
    payload = {
        "model": config.get("model"),
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0,
    }
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout_seconds,
        )
    except httpx.HTTPError as exc:
        return {
            "kind": "provider_unavailable",
            "code": "provider_preflight_http_error",
            "category": "provider_model_error",
            "message": f"{type(exc).__name__}: {exc}",
            "action": "inspect_provider_error_and_model_config",
            "model_config": _safe_preflight_model_config(config),
        }
    if response.status_code < 400:
        return None
    if response.status_code in {401, 402, 403, 404, 429}:
        code = _provider_code_from_preflight_response(response)
        return {
            "kind": "provider_unavailable",
            "http_status": response.status_code,
            "code": code,
            "category": _provider_category_for_code(code, response.status_code),
            "message": _safe_provider_message(_provider_message_from_response(response)),
            "action": _provider_action_for_code(code, response.status_code),
            "model_config": _safe_preflight_model_config(config),
        }
    return {
        "kind": "provider_unavailable",
        "http_status": response.status_code,
        "code": "provider_preflight_failed",
        "category": "provider_model_error",
        "message": _safe_provider_message(_provider_message_from_response(response)),
        "action": "inspect_provider_error_and_model_config",
        "model_config": _safe_preflight_model_config(config),
    }


def _preflight_config_from_env(model_value: str | None) -> dict[str, Any] | None:
    dotenv = _load_dotenv_values()
    provider_value = str(model_value or os.getenv("LLM_PROVIDER") or dotenv.get("LLM_PROVIDER") or "").strip()
    if not provider_value:
        return None
    provider, _, model_override = provider_value.partition(":")
    provider = provider.strip().lower()
    model_override = model_override.strip()
    if provider == "deepseek":
        return {
            "provider": "deepseek",
            "provider_value": provider_value,
            "base_url": os.getenv("DEEPSEEK_BASE_URL") or dotenv.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
            "api_key": os.getenv("DEEPSEEK_API_KEY") or dotenv.get("DEEPSEEK_API_KEY"),
            "model": model_override or os.getenv("DEEPSEEK_MODEL_PLANNER") or dotenv.get("DEEPSEEK_MODEL_PLANNER") or "deepseek-chat",
        }
    if provider == "openai":
        return {
            "provider": "openai",
            "provider_value": provider_value,
            "base_url": os.getenv("OPENAI_BASE_URL") or dotenv.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            "api_key": os.getenv("OPENAI_API_KEY") or dotenv.get("OPENAI_API_KEY"),
            "model": model_override or os.getenv("OPENAI_MODEL_PLANNER") or dotenv.get("OPENAI_MODEL_PLANNER") or "gpt-4o-mini",
        }
    if provider == "qwen":
        return {
            "provider": "qwen",
            "provider_value": provider_value,
            "base_url": os.getenv("QWEN_BASE_URL") or dotenv.get("QWEN_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": os.getenv("DASHSCOPE_API_KEY") or dotenv.get("DASHSCOPE_API_KEY"),
            "model": model_override or os.getenv("QWEN_MODEL_PLANNER") or dotenv.get("QWEN_MODEL_PLANNER") or "qwen3.5-flash",
        }
    return None


def _load_dotenv_values(path: Path | None = None) -> dict[str, str]:
    env_path = path or Path.cwd() / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _safe_preflight_model_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider_value": config.get("provider_value"),
        "provider": config.get("provider"),
        "base_url": str(config.get("base_url") or "").rstrip("/"),
        "model": config.get("model"),
        "api_key_set": bool(config.get("api_key")),
    }


def _provider_code_from_preflight_response(response: Any) -> str:
    text = _provider_message_from_response(response)
    if response.status_code == 402 or any(token in text for token in ("Insufficient Balance", "Payment Required", "余额不足")):
        return "provider_billing_unavailable"
    if response.status_code in {401, 403}:
        return "provider_auth_failed"
    if response.status_code == 404:
        return "provider_model_not_found"
    if response.status_code == 429:
        return "provider_rate_limited"
    return "provider_preflight_failed"


def _provider_message_from_response(response: Any) -> str:
    try:
        body = response.json()
    except Exception:
        return str(getattr(response, "text", "") or "")
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or body)
    return json.dumps(body, ensure_ascii=False)


def _provider_category_for_code(code: str, http_status: int | None) -> str:
    if code == "provider_billing_unavailable":
        return "provider_billing_unavailable"
    if code == "provider_auth_failed":
        return "provider_auth"
    if code == "provider_rate_limited" or http_status == 429:
        return "provider_rate_limit"
    return "provider_model_error"


def _provider_action_for_code(code: str, http_status: int | None) -> str:
    if code == "provider_billing_unavailable":
        return "recharge_provider_or_switch_model"
    if code == "provider_auth_failed":
        return "check_api_key_model_permission_or_provider_config"
    if code == "provider_rate_limited" or http_status == 429:
        return "retry_later_or_switch_model"
    return "inspect_provider_error_and_model_config"


def _safe_provider_message(value: Any) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text[:240]


def _final(result: dict[str, Any]) -> dict[str, Any]:
    agent_result = result.get("agent_result") if isinstance(result.get("agent_result"), dict) else {}
    final = agent_result.get("final")
    if isinstance(final, dict):
        return final
    answer = result.get("answer")
    return answer if isinstance(answer, dict) else {}


def _tool_call_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
    return names


def _string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _active_skill_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    for item in value:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str):
            ids.append(item["id"])
    return ids


def _visible_text(value: Any) -> str:
    pieces: list[str] = []
    for item in _iter_strings(value):
        if item.strip():
            pieces.append(item.strip())
    return "\n".join(pieces)


def _iter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        items: list[str] = []
        for child in value.values():
            items.extend(_iter_strings(child))
        return items
    if isinstance(value, list):
        items: list[str] = []
        for child in value:
            items.extend(_iter_strings(child))
        return items
    return []


def _restaurant_recommendation_count(result: dict[str, Any]) -> int:
    recs = _final(result).get("recommendations")
    if not isinstance(recs, list):
        return 0
    return sum(1 for item in recs if isinstance(item, dict) and item.get("type") == "restaurant")


def _itinerary_day_count(final: dict[str, Any]) -> int:
    itinerary = final.get("itinerary")
    days = itinerary.get("days") if isinstance(itinerary, dict) else None
    return len(days) if isinstance(days, list) else 0


def _candidate_names(final: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for key in ("candidates",):
        value = final.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("title") or item.get("source_name") or "").strip()
                if name:
                    names.append(name)
    groups = final.get("candidate_groups")
    attractions = groups.get("attractions") if isinstance(groups, dict) else None
    if isinstance(attractions, list):
        for item in attractions:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("title") or item.get("source_name") or "").strip()
                if name:
                    names.append(name)
    return names


def _itinerary_place_names(final: dict[str, Any]) -> list[str]:
    itinerary = final.get("itinerary")
    days = itinerary.get("days") if isinstance(itinerary, dict) else None
    names: list[str] = []
    if not isinstance(days, list):
        return names
    for day in days:
        items = day.get("items") if isinstance(day, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                name = str(item.get("place_name") or item.get("name") or item.get("title") or "").strip()
                if name:
                    names.append(name)
    return names


def _excluded_place_names(final: dict[str, Any]) -> list[str]:
    names: list[str] = []
    value = final.get("excluded_places")
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("title") or item.get("source_name") or "").strip()
            else:
                name = str(item or "").strip()
            if name:
                names.append(name)
    groups = final.get("candidate_groups")
    excluded = groups.get("excluded") if isinstance(groups, dict) else None
    if isinstance(excluded, list):
        for item in excluded:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("title") or item.get("source_name") or "").strip()
                if name:
                    names.append(name)
    return names


def _prompt_artifact_place_names(final: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for key in ("places", "candidates"):
        value = final.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("title") or item.get("source_name") or "").strip()
            if len(name) > 20 and any(token in name for token in ("我可以", "您可以", "请补充", "高德验证POI", "有什么特别想去")):
                names.append(name)
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description="Run live multi-turn dialogue quality probes against Smart Eats backend")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model-value", default=None)
    parser.add_argument("--out", default="/tmp/smarteats_dialogue_quality_probe.json")
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--request-delay-seconds", type=float, default=0.5)
    parser.add_argument("--persist-wait-seconds", type=float, default=20.0)
    parser.add_argument("--persist-poll-seconds", type=float, default=0.25)
    parser.add_argument("--scenario-id", action="append", default=[])
    parser.add_argument(
        "--continue-on-provider-unavailable",
        action="store_true",
        help="Keep running scenarios after provider auth/billing errors. Default stops early to avoid noisy failures.",
    )
    parser.add_argument(
        "--skip-provider-preflight",
        action="store_true",
        help="Skip direct provider preflight and discover provider errors through the backend stream.",
    )
    args = parser.parse_args()

    import httpx

    selected = set(args.scenario_id or [])
    scenarios = [item for item in SCENARIOS if not selected or item.get("id") in selected]
    short_scenarios = [
        str(item.get("id"))
        for item in scenarios
        if len([turn for turn in item.get("turns") or [] if isinstance(turn, dict)]) < 5
    ]
    if short_scenarios:
        raise SystemExit(f"dialogue scenarios must have at least 5 turns: {', '.join(short_scenarios)}")

    provider_unavailable = None
    if not args.skip_provider_preflight:
        provider_unavailable = preflight_provider(args.model_value, timeout_seconds=min(args.timeout_seconds, 30.0))
    if provider_unavailable:
        report = {
            "total_planned": len(scenarios),
            "total": 0,
            "passed_count": 0,
            "pass_rate": None,
            "quality_metrics": aggregate_quality_metrics(
                [],
                total_planned=len(scenarios),
                provider_unavailable=provider_unavailable,
            ),
            "provider_unavailable": provider_unavailable,
            "failed": [],
            "results": [],
        }
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    results = []
    with httpx.Client(base_url=args.base_url, timeout=args.timeout_seconds) as client:
        for scenario in scenarios:
            result = run_scenario(
                client,
                scenario,
                model_value=args.model_value,
                timeout_seconds=args.timeout_seconds,
                request_delay_seconds=args.request_delay_seconds,
                persist_wait_seconds=args.persist_wait_seconds,
                persist_poll_seconds=args.persist_poll_seconds,
            )
            results.append(result)
            if result.get("provider_unavailable") and not args.continue_on_provider_unavailable:
                break
    provider_unavailable = next((item.get("provider_unavailable") for item in results if item.get("provider_unavailable")), None)
    failed = [item for item in results if not item.get("passed") and not item.get("provider_unavailable")]
    passed_count = sum(1 for item in results if item.get("passed"))
    report = {
        "total_planned": len(scenarios),
        "total": len(results),
        "passed_count": passed_count,
        "pass_rate": (passed_count / len(results)) if results and not provider_unavailable else None,
        "quality_metrics": aggregate_quality_metrics(results, total_planned=len(scenarios), provider_unavailable=provider_unavailable),
        "provider_unavailable": provider_unavailable,
        "failed": [{"id": item.get("id"), "session_id": item.get("session_id"), "violations": item.get("violations")} for item in failed],
        "results": results,
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if provider_unavailable:
        raise SystemExit(2)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
