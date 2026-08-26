from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from libs.common.errors import ForbiddenError
from libs.security.principal import Principal, Role
from libs.security.rbac import require_roles
from services.trip.container import get_redis, get_trip_service
from services.trip.schemas import (
    CancelRequest, PaymentResultRequest, RateAnnotationRequest, RequestRideRequest, RespondRequest, TripResponse,
)
from services.trip.service import TripService
from services.trip.ws_fanout import driver_channel, trip_channel

router = APIRouter()


def _to_response(trip) -> TripResponse:
    return TripResponse(**{k: getattr(trip, k) for k in TripResponse.model_fields})


@router.post("/v1/trips", response_model=TripResponse)
async def request_ride(
    body: RequestRideRequest,
    principal: Principal = Depends(require_roles(Role.RIDER)),
    svc: TripService = Depends(get_trip_service),
):
    trip = await svc.request_ride(
        principal.user_id, (body.pickup.lat, body.pickup.lng), (body.drop.lat, body.drop.lng), body.vehicle_type,
    )
    return _to_response(trip)


@router.get("/v1/drivers/{driver_id}/current-offer")
async def get_current_offer(
    driver_id: str,
    principal: Principal = Depends(require_roles(Role.DRIVER, Role.ADMIN)),
    svc: TripService = Depends(get_trip_service),
):
    if principal.role == Role.DRIVER and principal.user_id != driver_id:
        raise ForbiddenError("a driver can only read their own offer")
    trip = await svc.get_current_offer(driver_id)
    if trip is None:
        return {"offer": None}
    return {
        "offer": {
            "trip_id": trip.trip_id, "offer_id": trip.current_offer_id,
            "expires_at": trip.current_offer_expires_at.isoformat(),
            "pickup_lat": trip.pickup_lat, "pickup_lng": trip.pickup_lng,
            "drop_lat": trip.drop_lat, "drop_lng": trip.drop_lng,
            "fare_estimate": trip.fare_estimate,
        }
    }


@router.get("/v1/trips/mine", response_model=list[TripResponse])
async def list_my_trips(
    principal: Principal = Depends(require_roles(Role.RIDER)),
    svc: TripService = Depends(get_trip_service),
):
    trips = await svc.list_for_rider(principal.user_id)
    return [_to_response(t) for t in trips]


@router.get("/v1/trips", response_model=list[TripResponse])
async def list_recent_trips(
    _principal: Principal = Depends(require_roles(Role.ADMIN)),
    svc: TripService = Depends(get_trip_service),
):
    trips = await svc.list_recent()
    return [_to_response(t) for t in trips]


@router.get("/v1/trips/{trip_id}", response_model=TripResponse)
async def get_trip(
    trip_id: str, principal: Principal = Depends(require_roles(Role.RIDER, Role.DRIVER, Role.ADMIN)),
    svc: TripService = Depends(get_trip_service),
):
    trip = await svc.get(trip_id, principal)
    return _to_response(trip)


@router.get("/v1/trips/{trip_id}/audit")
async def get_audit(
    trip_id: str, principal: Principal = Depends(require_roles(Role.RIDER, Role.DRIVER, Role.ADMIN)),
    svc: TripService = Depends(get_trip_service),
):
    return await svc.audit_trail(trip_id, principal)


@router.post("/v1/trips/{trip_id}/respond", response_model=TripResponse)
async def respond(
    trip_id: str, body: RespondRequest,
    principal: Principal = Depends(require_roles(Role.DRIVER)),
    svc: TripService = Depends(get_trip_service),
):
    trip = await svc.respond_to_offer(trip_id, principal.user_id, body.action)
    return _to_response(trip)


@router.post("/v1/trips/{trip_id}/start-navigation", response_model=TripResponse)
async def start_navigation(
    trip_id: str, principal: Principal = Depends(require_roles(Role.DRIVER, Role.ADMIN)),
    svc: TripService = Depends(get_trip_service),
):
    return _to_response(await svc.start_navigation(trip_id, principal))


@router.post("/v1/trips/{trip_id}/confirm-arrival", response_model=TripResponse)
async def confirm_arrival(
    trip_id: str, principal: Principal = Depends(require_roles(Role.DRIVER, Role.ADMIN)),
    svc: TripService = Depends(get_trip_service),
):
    return _to_response(await svc.confirm_arrival(trip_id, principal))


@router.post("/v1/trips/{trip_id}/start", response_model=TripResponse)
async def start_trip(
    trip_id: str, principal: Principal = Depends(require_roles(Role.DRIVER, Role.ADMIN)),
    svc: TripService = Depends(get_trip_service),
):
    return _to_response(await svc.start_trip(trip_id, principal))


@router.post("/v1/trips/{trip_id}/complete", response_model=TripResponse)
async def complete_trip(
    trip_id: str, principal: Principal = Depends(require_roles(Role.DRIVER, Role.ADMIN)),
    svc: TripService = Depends(get_trip_service),
):
    return _to_response(await svc.complete_trip(trip_id, principal))


@router.post("/v1/trips/{trip_id}/cancel", response_model=TripResponse)
async def cancel_trip(
    trip_id: str, body: CancelRequest,
    principal: Principal = Depends(require_roles(Role.RIDER, Role.ADMIN)),
    svc: TripService = Depends(get_trip_service),
):
    return _to_response(await svc.cancel(trip_id, principal, body.reason))


@router.post("/v1/trips/{trip_id}/driver-cancel", response_model=TripResponse)
async def driver_cancel_trip(
    trip_id: str, principal: Principal = Depends(require_roles(Role.DRIVER)),
    svc: TripService = Depends(get_trip_service),
):
    return _to_response(await svc.driver_cancel(trip_id, principal.user_id))


@router.post("/v1/trips/{trip_id}/payment-result", response_model=TripResponse)
async def payment_result(
    trip_id: str, body: PaymentResultRequest,
    _principal: Principal = Depends(require_roles(Role.ADMIN)),  # internal call from Payment service only
    svc: TripService = Depends(get_trip_service),
):
    if body.status == "SUCCEEDED":
        trip = await svc.mark_payment_result(trip_id, succeeded=True)
    elif body.status == "PAID_PENDING_RETRY_SUCCEEDED":
        trip = await svc.mark_payment_retry_succeeded(trip_id)
    else:
        trip = await svc.mark_payment_result(trip_id, succeeded=False)
    return _to_response(trip)


@router.post("/v1/trips/{trip_id}/rate-annotation", response_model=TripResponse)
async def rate_annotation(
    trip_id: str, body: RateAnnotationRequest,
    _principal: Principal = Depends(require_roles(Role.ADMIN)),  # internal call from Ratings service only
    svc: TripService = Depends(get_trip_service),
):
    return _to_response(await svc.rate_trip_annotation(trip_id, body.rater_role, body.rater_id))


async def _ws_pubsub_relay(ws: WebSocket, channel: str) -> None:
    """Shared WS mechanics for both rider trip-tracking and driver offer channels
    (PLAN amendment AM-07: Redis pub/sub fan-out, so this works unchanged if this
    service ever runs as more than one instance)."""
    redis = get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    await ws.accept()
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            await ws.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()


@router.websocket("/v1/ws/trips/{trip_id}/track")
async def ws_track_trip(ws: WebSocket, trip_id: str):
    # NOTE (GAP, documented not hidden): WS auth tonight is a query-param token
    # rather than a header, since browsers cannot set custom headers on the
    # WebSocket handshake. Verified the same way as any bearer token.
    from libs.common.config import get_settings
    from libs.security.jwt_tokens import decode_token

    token = ws.query_params.get("token")
    settings = get_settings()
    try:
        decode_token(token or "", secret=settings.jwt_secret, algorithm=settings.jwt_algorithm)
    except Exception:
        await ws.close(code=4401)
        return
    await _ws_pubsub_relay(ws, trip_channel(trip_id))


@router.websocket("/v1/ws/drivers/{driver_id}/offers")
async def ws_driver_offers(ws: WebSocket, driver_id: str):
    from libs.common.config import get_settings
    from libs.security.jwt_tokens import decode_token

    token = ws.query_params.get("token")
    settings = get_settings()
    try:
        principal = decode_token(token or "", secret=settings.jwt_secret, algorithm=settings.jwt_algorithm)
    except Exception:
        await ws.close(code=4401)
        return
    if principal.user_id != driver_id and principal.role != Role.ADMIN:
        await ws.close(code=4403)
        return
    await _ws_pubsub_relay(ws, driver_channel(driver_id))
