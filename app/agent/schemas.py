from __future__ import annotations

from typing import Literal, Union, Any

from pydantic import BaseModel, Field, RootModel


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


class IntentDecision(BaseModel):
    intent: Literal["eat_out", "cook_home", "route", "chat", "unknown"] = "unknown"
    confidence: float = 0.0
    slots: dict[str, Any] = Field(default_factory=dict)
    need_clarify: bool = False
    clarify_question: str | None = None
