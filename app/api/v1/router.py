from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import app, auth, chat, context, decisions, fridge, games, group_decisions, internal, preferences, recipes, restaurants, today, users

router = APIRouter(prefix="/api/v1")

router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(app.router, prefix="/app", tags=["app"])
router.include_router(users.router, prefix="/users", tags=["users"])
router.include_router(chat.router, prefix="/chat", tags=["chat"])
router.include_router(context.router, prefix="/context", tags=["context"])
router.include_router(today.router, prefix="/today", tags=["today"])
router.include_router(decisions.router, prefix="/decisions", tags=["decisions"])
router.include_router(group_decisions.router, tags=["group_decisions"])
router.include_router(preferences.router, tags=["preferences"])
router.include_router(fridge.router, prefix="/fridge", tags=["fridge"])
router.include_router(restaurants.router, prefix="/restaurants", tags=["restaurants"])
router.include_router(recipes.router, prefix="/recipes", tags=["recipes"])
router.include_router(games.router, prefix="/games", tags=["games"])
router.include_router(internal.router, prefix="/internal", tags=["internal"])
