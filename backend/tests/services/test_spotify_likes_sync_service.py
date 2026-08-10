from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from infrastructure.persistence.spotify_likes_store import SpotifyLikesSettings
from services.spotify_likes_sync_service import SpotifyLikesSyncService


def _settings(**updates) -> SpotifyLikesSettings:
    values = {
        "user_id": "user-1",
        "enabled": True,
        "include_existing": False,
        "initialized": False,
        "requires_reconnect": False,
        "enabled_at": "2026-08-05T00:00:00+00:00",
        "last_sync_at": None,
        "last_error": None,
        "updated_at": "",
    }
    values.update(updates)
    return SpotifyLikesSettings(**values)


def _service(*, settings=None, client=None):
    store = AsyncMock()
    store.get_settings = AsyncMock(return_value=settings or _settings())
    store.recent_track_ids = AsyncMock(return_value=set())
    store.add_tracks = AsyncMock(return_value=1)
    store.pending_tracks = AsyncMock(return_value=[])
    store.counts = AsyncMock(return_value={})

    clients = AsyncMock()
    clients.resolve_spotify = AsyncMock(return_value=client)

    auth = AsyncMock()
    auth.get_user_by_id = AsyncMock(
        return_value=SimpleNamespace(id="user-1", role="admin")
    )
    service = SpotifyLikesSyncService(
        store=store,
        client_factory=clients,
        musicbrainz=AsyncMock(),
        acquisition=AsyncMock(),
        quota=AsyncMock(),
        auth=auth,
    )
    return service, store


@pytest.mark.asyncio
async def test_existing_link_requires_one_reconnect_for_library_scope():
    client = SimpleNamespace(has_library_scope=False)
    service, store = _service(client=client)

    await service.update_settings("user-1", enabled=True, include_existing=False)

    store.update_settings.assert_awaited_once_with(
        "user-1",
        enabled=True,
        include_existing=False,
        requires_reconnect=True,
        clear_error=False,
        last_error="Reconnect Spotify to grant access to liked tracks",
    )


@pytest.mark.asyncio
async def test_first_sync_baselines_existing_likes_when_backfill_is_off():
    client = AsyncMock()
    client.has_library_scope = True
    client.get_saved_tracks = AsyncMock(
        return_value=[
            {
                "added_at": "2026-08-01T00:00:00Z",
                "track": {
                    "id": "spotify-1",
                    "name": "Song",
                    "type": "track",
                    "artists": [{"name": "Artist"}],
                    "album": {"name": "Album"},
                    "duration_ms": 180000,
                    "external_ids": {"isrc": "USABC1234567"},
                },
            }
        ]
    )
    service, store = _service(client=client)

    await service.sync_user("user-1")

    store.add_tracks.assert_awaited_once()
    assert store.add_tracks.await_args.args[2] == "ignored"
    service._acquisition.request_track.assert_not_awaited()


@pytest.mark.asyncio
async def test_first_sync_queues_likes_added_after_feature_was_enabled():
    client = AsyncMock()
    client.has_library_scope = True
    client.get_saved_tracks = AsyncMock(
        return_value=[
            {
                "added_at": "2026-08-06T00:00:00Z",
                "track": {
                    "id": "spotify-new",
                    "name": "New Song",
                    "type": "track",
                    "artists": [{"name": "Artist"}],
                    "album": {"name": "Album"},
                    "duration_ms": 180000,
                    "external_ids": {"isrc": "USABC1234567"},
                },
            }
        ]
    )
    service, store = _service(client=client)

    await service.sync_user("user-1")

    store.add_tracks.assert_awaited_once()
    assert store.add_tracks.await_args.args[2] == "pending"


@pytest.mark.asyncio
async def test_initialized_sync_requests_a_new_liked_track(monkeypatch):
    client = AsyncMock()
    client.has_library_scope = True
    client.get_saved_tracks = AsyncMock(return_value=[])
    service, store = _service(client=client, settings=_settings(initialized=True))
    pending = SimpleNamespace(
        spotify_track_id="spotify-2",
        artist_name="Artist",
        track_title="New Song",
        album_title="Album",
        duration_seconds=200,
        isrc="USABC7654321",
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
        origin="spotify_liked",
        release_mbid="release-mbid",
    )
    store.finish_track.assert_awaited_once_with(
        "user-1",
        "spotify-2",
        status="requested",
        recording_mbid="recording-mbid",
        release_group_mbid="release-group-mbid",
        request_task_id="task-1",
    )
