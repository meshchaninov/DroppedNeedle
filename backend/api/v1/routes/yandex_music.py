"""Yandex Music liked-track synchronization endpoints."""

import asyncio

from fastapi import APIRouter, Depends

from core.dependencies import get_yandex_music_likes_sync_service
from core.task_registry import TaskRegistry
from infrastructure.msgspec_fastapi import AppStruct, MsgSpecBody, MsgSpecRoute
from middleware import CurrentUserDep
from services.yandex_music_likes_sync_service import YandexMusicLikesSyncService

router = APIRouter(route_class=MsgSpecRoute, prefix="/me/yandex-music", tags=["yandex-music"])


class YandexMusicLikesSettingsUpdate(AppStruct):
    enabled: bool
    include_existing: bool = False


class YandexMusicLikesStatus(AppStruct):
    enabled: bool = False
    include_existing: bool = False
    initialized: bool = False
    last_sync_at: str | None = None
    last_error: str | None = None
    pending: int = 0
    requested: int = 0
    already_in_library: int = 0
    unmatched: int = 0
    failed: int = 0
    ignored: int = 0


class YandexMusicLikesSyncResponse(AppStruct):
    status: str = "started"


def _status(data: dict) -> YandexMusicLikesStatus:
    return YandexMusicLikesStatus(**data)


@router.get("/liked-sync", response_model=YandexMusicLikesStatus)
async def get_liked_sync_status(
    current_user: CurrentUserDep,
    service: YandexMusicLikesSyncService = Depends(get_yandex_music_likes_sync_service),
) -> YandexMusicLikesStatus:
    return _status(await service.status(current_user.id))


@router.put("/liked-sync", response_model=YandexMusicLikesStatus)
async def update_liked_sync(
    body: YandexMusicLikesSettingsUpdate = MsgSpecBody(YandexMusicLikesSettingsUpdate),
    current_user: CurrentUserDep = None,
    service: YandexMusicLikesSyncService = Depends(get_yandex_music_likes_sync_service),
) -> YandexMusicLikesStatus:
    await service.update_settings(
        current_user.id,
        enabled=body.enabled,
        include_existing=body.include_existing,
    )
    if body.enabled:
        task_key = f"yandex-music:liked-sync:{current_user.id}"
        registry = TaskRegistry.get_instance()
        if not registry.is_running(task_key):
            try:
                registry.register(task_key, asyncio.create_task(service.sync_user(current_user.id)))
            except RuntimeError:
                pass
    return _status(await service.status(current_user.id))


@router.post("/liked-sync/run", response_model=YandexMusicLikesSyncResponse)
async def run_liked_sync(
    current_user: CurrentUserDep,
    service: YandexMusicLikesSyncService = Depends(get_yandex_music_likes_sync_service),
) -> YandexMusicLikesSyncResponse:
    task_key = f"yandex-music:liked-sync:{current_user.id}"
    registry = TaskRegistry.get_instance()
    if registry.is_running(task_key):
        return YandexMusicLikesSyncResponse(status="already_running")
    try:
        registry.register(
            task_key,
            asyncio.create_task(service.sync_user(current_user.id, force=True)),
        )
    except RuntimeError:
        return YandexMusicLikesSyncResponse(status="already_running")
    return YandexMusicLikesSyncResponse()
