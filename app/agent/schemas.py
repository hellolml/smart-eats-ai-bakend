from __future__ import annotations

from typing import Literal, Union, Any

from pydantic import BaseModel, Field, RootModel, ConfigDict


class RecipeRecommendation(BaseModel):
    type: Literal["recipe"]
    title: str
    reason: str | None = None
    calories: int | None = None
    time: int | None = None
    tags: list[str] = Field(default_factory=list)
    image_url: str | None = None


class RestaurantRecommendation(BaseModel):
    type: Literal["restaurant"]
    title: str
    reason: str | None = None
    rating: float | None = None
    price: int | None = None
    tags: list[str] = Field(default_factory=list)
    geo: dict | None = None


class NoteRecommendation(BaseModel):
    type: Literal["note"]
    title: str
    reason: str | None = None


Recommendation = Union[RecipeRecommendation, RestaurantRecommendation, NoteRecommendation]


class FinalAnswerArgs(BaseModel):
    model_config = ConfigDict(extra="allow")

    recommendations: list[Recommendation] = Field(default_factory=list, description="推荐的美食、餐厅或笔记列表")
    followups: list[str] = Field(default_factory=list, description="追问或引导用户下一步的话术")
    warnings: list[str] = Field(default_factory=list, description="需要提醒用户的注意事项")
    state: str | None = Field(default=None, description="Optional workflow state, e.g. candidates_ready or itinerary_generated.")
    await_confirmation: bool | None = Field(default=None, description="Whether the client should wait for user confirmation.")
    trip_meta: dict[str, Any] | None = Field(default=None, description="Travel planning metadata.")
    sources: list[dict[str, Any]] | None = Field(default=None, description="Travel content sources.")
    places: list[dict[str, Any]] | None = Field(default=None, description="Extracted places.")
    candidates: list[dict[str, Any]] | None = Field(default=None, description="Verified candidate POIs.")
    failed_places: list[dict[str, Any]] | None = Field(default=None, description="Places that failed POI verification.")
    itinerary: dict[str, Any] | None = Field(default=None, description="Structured itinerary.")
    map: dict[str, Any] | None = Field(default=None, description="AMap map payload.")
    raw_text: str | None = Field(default=None, description="Raw travel plan text.")


class FinalAnswer(BaseModel):
    recommendations: list[Recommendation] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ToolAction(BaseModel):
    type: Literal["tool"] = "tool"
    name: str
    args: dict = Field(default_factory=dict)


class ToolCallsAction(BaseModel):
    type: Literal["tool_calls"] = "tool_calls"
    calls: list[dict[str, dict[str, Any]]] = Field(default_factory=list)


class FinalAction(BaseModel):
    type: Literal["final"] = "final"
    answer: FinalAnswer


AgentAction = Union[ToolAction, ToolCallsAction, FinalAction]


class AgentActionModel(RootModel[AgentAction]):
    pass


class IntentDecisionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["eat_out", "cook_home", "route", "chat", "unknown"] = Field(
        ..., description="用户本轮的主意图"
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="意图置信度，范围 0~1")
    slots: dict[str, Any] = Field(default_factory=dict, description="提取到的结构化槽位")
    need_clarify: bool = Field(default=False, description="是否需要进一步澄清")
    clarify_question: str | None = Field(default=None, description="需要澄清时给用户的问题")


class IntentDecision(BaseModel):
    intent: Literal["eat_out", "cook_home", "route", "chat", "unknown"] = "unknown"
    confidence: float = 0.0
    slots: dict[str, Any] = Field(default_factory=dict)
    need_clarify: bool = False
    clarify_question: str | None = None
