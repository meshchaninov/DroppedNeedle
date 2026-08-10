from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import services.yandex_music_client as client_module
from services.yandex_music_client import YandexMusicClient


@pytest.mark.asyncio
async def test_adapter_uses_library_models_for_account_and_likes(monkeypatch):
    library_client = SimpleNamespace(
        account_uid=None,
        account_status=AsyncMock(
            return_value=SimpleNamespace(
                account=SimpleNamespace(uid=42, display_name="Alice", login="alice")
            )
        ),
        users_likes_tracks=AsyncMock(
            return_value=SimpleNamespace(
                tracks=[
                    SimpleNamespace(
                        id="101",
                        timestamp="2026-08-10T01:00:00Z",
                        track_id="101:201",
                    )
                ]
            )
        ),
        tracks=AsyncMock(
            return_value=[
                SimpleNamespace(
                    id="101",
                    title="Song",
                    artists=[SimpleNamespace(name="Artist")],
                    albums=[SimpleNamespace(title="Album")],
                    duration_ms=180000,
                )
            ]
        ),
    )
    monkeypatch.setattr(
        client_module, "ClientAsync", lambda _token, language: library_client
    )
    client = YandexMusicClient("secret-token")

    account = await client.get_account()
    tracks = await client.get_liked_tracks()

    assert account == {"uid": "42", "username": "Alice"}
    library_client.users_likes_tracks.assert_awaited_once_with("42")
    library_client.tracks.assert_awaited_once_with(["101:201"])
    assert tracks == [
        {
            "added_at": "2026-08-10T01:00:00Z",
            "track": {
                "id": "101",
                "title": "Song",
                "artists": [{"name": "Artist"}],
                "albums": [{"title": "Album"}],
                "duration_ms": 180000,
            },
        }
    ]
