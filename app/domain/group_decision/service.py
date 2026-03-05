from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.models.group_decision import GroupDecisionSession, GroupVote, GroupVoteItem


def _share_url(base_url: str, session_id: str, share_token: str) -> str:
    root = base_url.rstrip("/")
    return f"{root}/#/group-decision/{session_id}?token={share_token}"


class GroupDecisionService:
    @staticmethod
    async def create_session(
        db: AsyncSession,
        *,
        creator_user_id: str,
        title: str,
        options: list[dict[str, Any]],
        city: str | None,
        base_url: str,
        expires_hours: int = 24,
    ) -> dict[str, Any]:
        if not options:
            raise HTTPException(status_code=400, detail="options required")

        session_id = str(uuid4())
        share_token = uuid4().hex
        now = datetime.now(timezone.utc)
        session = GroupDecisionSession(
            id=session_id,
            creator_user_id=creator_user_id,
            title=(title or "今晚吃什么")[:128],
            city=city,
            status="open",
            share_token=share_token,
            expires_at=now + timedelta(hours=max(1, min(expires_hours, 168))),
        )
        db.add(session)

        created_items: list[GroupVoteItem] = []
        for raw in options[:12]:
            item = GroupVoteItem(
                id=str(uuid4()),
                session_id=session_id,
                title=str(raw.get("title") or "")[:128],
                item_type=str(raw.get("item_type") or "restaurant")[:24],
                meta_json=raw.get("meta") if isinstance(raw.get("meta"), dict) else {},
                score=0.0,
            )
            if not item.title:
                continue
            db.add(item)
            created_items.append(item)

        if not created_items:
            raise HTTPException(status_code=400, detail="no valid options")

        await db.commit()
        share_link = _share_url(base_url, session_id, share_token)
        return {
            "id": session_id,
            "title": session.title,
            "city": session.city,
            "status": session.status,
            "share_url": share_link,
            "share_token": share_token,
            "items": [
                {
                    "id": item.id,
                    "title": item.title,
                    "item_type": item.item_type,
                    "meta": item.meta_json or {},
                    "votes": 0,
                }
                for item in created_items
            ],
        }

    @staticmethod
    async def submit_vote(
        db: AsyncSession,
        *,
        session_id: str,
        item_id: str,
        voter_name: str,
        voter_key: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        session = (
            await db.execute(select(GroupDecisionSession).where(GroupDecisionSession.id == session_id))
        ).scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=404, detail="group decision not found")
        if session.status != "open":
            raise HTTPException(status_code=400, detail="group decision already closed")

        item = (
            await db.execute(
                select(GroupVoteItem).where(
                    GroupVoteItem.id == item_id,
                    GroupVoteItem.session_id == session_id,
                )
            )
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="vote item not found")

        await db.execute(
            delete(GroupVote).where(GroupVote.session_id == session_id, GroupVote.voter_key == voter_key)
        )
        vote = GroupVote(
            id=str(uuid4()),
            session_id=session_id,
            item_id=item_id,
            voter_name=voter_name[:64],
            voter_key=voter_key[:64],
            note=(note or "")[:300] or None,
        )
        db.add(vote)
        await db.commit()
        return {"ok": True, "session_id": session_id, "item_id": item_id}

    @staticmethod
    async def get_result(
        db: AsyncSession,
        *,
        session_id: str,
        base_url: str,
    ) -> dict[str, Any]:
        session = (
            await db.execute(select(GroupDecisionSession).where(GroupDecisionSession.id == session_id))
        ).scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=404, detail="group decision not found")

        items = (
            await db.execute(select(GroupVoteItem).where(GroupVoteItem.session_id == session_id))
        ).scalars().all()
        counts = (
            await db.execute(
                select(GroupVote.item_id, func.count(GroupVote.id))
                .where(GroupVote.session_id == session_id)
                .group_by(GroupVote.item_id)
            )
        ).all()
        vote_count_map = {item_id: int(cnt) for item_id, cnt in counts}

        ranked = sorted(
            [
                {
                    "id": item.id,
                    "title": item.title,
                    "item_type": item.item_type,
                    "meta": item.meta_json or {},
                    "votes": vote_count_map.get(item.id, 0),
                }
                for item in items
            ],
            key=lambda x: x["votes"],
            reverse=True,
        )
        winner = ranked[0] if ranked else None
        share_link = _share_url(base_url, session_id, session.share_token)

        return {
            "id": session.id,
            "title": session.title,
            "city": session.city,
            "status": session.status,
            "share_url": share_link,
            "winner": winner,
            "items": ranked,
            "total_votes": sum(vote_count_map.values()),
        }
