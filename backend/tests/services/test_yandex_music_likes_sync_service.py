from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from infrastructure.persistence.yandex_music_likes_store import YandexMusicLikesSettings
from services.yandex_music_likes_sync_service import YandexMusicLikesSyncService


def _settings(**updates) -> YandexMusicLikesSettings:
    values = {
        "user_id": "user-1",
        "enabled": True,
        "include_existing": False,
        "initialized": False,
        "enabled_at": "2026-08-05T00:00:00+00:00",
        "last_sync_at": None,
        "last_error": None,
        "updated_at": "",
    }
    values.update(updates)
    return YandexMusicLikesSettings(**values)


def _service(*, settings=None, client=None):
    store = AsyncMock()
    store.get_settings = AsyncMock(return_value=settings or _settings())
    store.recent_track_ids = AsyncMock(return_value=set())
    store.add_tracks = AsyncMock(return_value=1)
    store.pending_tracks = AsyncMock(return_value=[])
    store.counts = AsyncMock(return_value={})

    clients = AsyncMock()
    clients.resolve_yandex_music = AsyncMock(return_value=client)
    auth = AsyncMock()
    auth.get_user_by_id = AsyncMock(return_value=SimpleNamespace(id="user-1", role="admin"))
    service = YandexMusicLikesSyncService(
        store=store,
        client_factory=clients,
        musicbrainz=AsyncMock(),
        acquisition=AsyncMock(),
        quota=AsyncMock(),
        auth=auth,
    )
    return service, store


def _liked_track(added_at: str = "2026-08-01T00:00:00Z") -> dict:
    return {
        "added_at": added_at,
        "track": {
            "id": "yandex-1",
            "title": "Song",
            "artists": [{"name": "Artist"}],
            "albums": [{"title": "Album"}],
            "duration_ms": 180000,
        },
    }


@pytest.mark.asyncio
async def test_first_sync_baselines_existing_yandex_likes():
    client = AsyncMock()
    client.get_liked_tracks = AsyncMock(return_value=[_liked_track()])
    service, store = _service(client=client)

    await service.sync_user("user-1")

    store.add_tracks.assert_awaited_once()
    assert store.add_tracks.await_args.args[2] == "ignored"
    service._acquisition.request_track.assert_not_awaited()


@pytest.mark.asyncio
async def test_first_sync_queues_yandex_likes_added_after_enablement():
    client = AsyncMock()
    client.get_liked_tracks = AsyncMock(return_value=[_liked_track("2026-08-06T00:00:00Z")])
    service, store = _service(client=client)

    await service.sync_user("user-1")

    assert store.add_tracks.await_args.args[2] == "pending"


@pytest.mark.asyncio
async def test_initialized_sync_uses_native_yandex_music_origin(monkeypatch):
    client = AsyncMock()
    client.get_liked_tracks = AsyncMock(return_value=[])
    service, store = _service(client=client, settings=_settings(initialized=True))
    pending = SimpleNamespace(
        yandex_track_id="yandex-2",
        artist_name="Artist",
        track_title="New Song",
        album_title="Album",
        duration_seconds=200,
        isrc=None,
    )
    store.pending_tracks = AsyncMock(return_value=[pending])
    monkeypatch.setattr(
        service,
        "_match",
        AsyncMock(return_value=("recording-mbid", "release-group-mbid", "release-mbid")),
    )
    service._acquisition.request_track = AsyncMock(return_value="task-1")

    await service.sync_user("user-1")

    service._acquisition.request_track.assert_awaited_once_with(
        user_id="user-1",
        recording_mbid="recording-mbid",
        artist_name="Artist",
        track_title="New Song",
        album_title="Album",
        duration_seconds=200,
        release_group_mbid="release-group-mbid",
        origin="yandex_music_liked",
        release_mbid="release-mbid",
    )
    store.finish_track.assert_awaited_once_with(
        "user-1",
        "yandex-2",
        status="requested",
        recording_mbid="recording-mbid",
        release_group_mbid="release-group-mbid",
        request_task_id="task-1",
    )
