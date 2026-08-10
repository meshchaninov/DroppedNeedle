import sqlite3

import pytest

from infrastructure.persistence.yandex_music_likes_store import YandexMusicLikesStore


@pytest.mark.asyncio
async def test_enabling_yandex_sync_records_timestamp_once(tmp_path):
    db_path = tmp_path / "library.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE auth_users (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO auth_users (id) VALUES ('user-1')")
    conn.commit()
    conn.close()
    store = YandexMusicLikesStore(db_path)

    await store.update_settings("user-1", enabled=True)
    enabled_at = (await store.get_settings("user-1")).enabled_at
    await store.update_settings("user-1", include_existing=True)

    assert enabled_at is not None
    assert (await store.get_settings("user-1")).enabled_at == enabled_at


@pytest.mark.asyncio
async def test_yandex_track_state_round_trip(tmp_path):
    db_path = tmp_path / "library.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE auth_users (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO auth_users (id) VALUES ('user-1')")
    conn.commit()
    conn.close()
    store = YandexMusicLikesStore(db_path)
    track = {
        "yandex_track_id": "123",
        "added_at": "2026-08-10T00:00:00Z",
        "artist_name": "Artist",
        "track_title": "Song",
        "album_title": "Album",
        "duration_seconds": 180,
        "isrc": None,
    }

    assert await store.add_tracks("user-1", [track], "pending") == 1
    pending = await store.pending_tracks("user-1")

    assert len(pending) == 1
    assert pending[0].yandex_track_id == "123"
