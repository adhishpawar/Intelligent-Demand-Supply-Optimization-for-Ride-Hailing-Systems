from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import text

from libs.security.principal import Principal
from libs.security.rbac import get_principal
from services.notification.container import get_redis, get_session
from services.notification.service import user_channel

router = APIRouter()


@router.get("/v1/notifications")
async def list_notifications(principal: Principal = Depends(get_principal), session=Depends(get_session)):
    rows = (
        await session.execute(
            text(
                "SELECT notification_id, type, title, body, read_at, created_at FROM notifications "
                "WHERE user_id = :id ORDER BY created_at DESC LIMIT 50"
            ),
            {"id": principal.user_id},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@router.post("/v1/notifications/{notification_id}/read")
async def mark_read(notification_id: str, principal: Principal = Depends(get_principal), session=Depends(get_session)):
    await session.execute(
        text("UPDATE notifications SET read_at = now() WHERE notification_id = :id AND user_id = :uid"),
        {"id": notification_id, "uid": principal.user_id},
    )
    await session.commit()
    return {"ok": True}


@router.websocket("/v1/ws/notifications/{user_id}")
async def ws_notifications(ws: WebSocket, user_id: str):
    from libs.common.config import get_settings
    from libs.security.jwt_tokens import decode_token

    token = ws.query_params.get("token")
    settings = get_settings()
    try:
        principal = decode_token(token or "", secret=settings.jwt_secret, algorithm=settings.jwt_algorithm)
    except Exception:
        await ws.close(code=4401)
        return
    if principal.user_id != user_id:
        await ws.close(code=4403)
        return

    redis = get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(user_channel(user_id))
    await ws.accept()
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            await ws.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(user_channel(user_id))
        await pubsub.aclose()
