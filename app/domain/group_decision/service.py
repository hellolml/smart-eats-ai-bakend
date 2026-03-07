from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.models.group_decision import GroupDecisionSession, GroupVote, GroupVoteItem

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"


def _share_url(base_url: str, session_id: str, share_token: str) -> str:
    root = base_url.rstrip("/")
    return f"{root}/#/group-decision/{session_id}?token={share_token}"


async def _load_session(db: AsyncSession, session_id: str) -> GroupDecisionSession:
    session = (
        await db.execute(select(GroupDecisionSession).where(GroupDecisionSession.id == session_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="group decision not found")
    return session


async def _calc_ranked_items(db: AsyncSession, session_id: str) -> tuple[list[dict[str, Any]], int]:
    items = (await db.execute(select(GroupVoteItem).where(GroupVoteItem.session_id == session_id))).scalars().all()
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
    return ranked, sum(vote_count_map.values())


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
            status=STATUS_OPEN,
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
        session = await _load_session(db, session_id)
        now = datetime.now(timezone.utc)
        expires_at = session.expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at and expires_at <= now and session.status != STATUS_CLOSED:
            session.status = STATUS_CLOSED
        if session.status != STATUS_OPEN:
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

        safe_voter_name = voter_name.strip()[:64]
        safe_voter_key = voter_key.strip()[:64]
        safe_note = (note or "")[:300] or None
        if not safe_voter_name or not safe_voter_key:
            raise HTTPException(status_code=400, detail="invalid voter")

        existing_vote = (
            await db.execute(
                select(GroupVote).where(GroupVote.session_id == session_id, GroupVote.voter_key == safe_voter_key)
            )
        ).scalar_one_or_none()

        if existing_vote is None:
            db.add(
                GroupVote(
                    id=str(uuid4()),
                    session_id=session_id,
                    item_id=item_id,
                    voter_name=safe_voter_name,
                    voter_key=safe_voter_key,
                    note=safe_note,
                )
            )
            changed = True
        else:
            changed = existing_vote.item_id != item_id or existing_vote.note != safe_note
            existing_vote.item_id = item_id
            existing_vote.voter_name = safe_voter_name
            existing_vote.note = safe_note

        await db.commit()
        return {
            "ok": True,
            "session_id": session_id,
            "item_id": item_id,
            "changed": changed,
        }

    @staticmethod
    async def close_session(
        db: AsyncSession,
        *,
        session_id: str,
        actor_user_id: str,
        base_url: str,
    ) -> dict[str, Any]:
        session = await _load_session(db, session_id)
        if session.creator_user_id != actor_user_id:
            raise HTTPException(status_code=403, detail="only creator can close this group decision")

        ranked, total_votes = await _calc_ranked_items(db, session_id)
        winner = ranked[0] if ranked else None

        session.status = STATUS_CLOSED
        session.closed_at = datetime.now(timezone.utc)
        session.winner_item_id = winner["id"] if winner else None
        session.total_votes = total_votes
        session.result_snapshot = {
            "winner": winner,
            "items": ranked,
            "total_votes": total_votes,
            "closed_at": session.closed_at.isoformat() if session.closed_at else None,
        }

        await db.commit()
        share_link = _share_url(base_url, session_id, session.share_token)
        return {
            "id": session.id,
            "title": session.title,
            "city": session.city,
            "status": session.status,
            "share_url": share_link,
            "winner": winner,
            "items": ranked,
            "total_votes": total_votes,
            "closed_at": session.closed_at,
        }

    @staticmethod
    async def get_result(
        db: AsyncSession,
        *,
        session_id: str,
        base_url: str,
    ) -> dict[str, Any]:
        session = await _load_session(db, session_id)

        if session.status == STATUS_CLOSED and isinstance(session.result_snapshot, dict) and session.result_snapshot:
            snapshot = session.result_snapshot
            ranked = snapshot.get("items") or []
            winner = snapshot.get("winner")
            total_votes = int(snapshot.get("total_votes") or 0)
        else:
            ranked, total_votes = await _calc_ranked_items(db, session_id)
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
            "total_votes": total_votes,
            "closed_at": session.closed_at,
        }
