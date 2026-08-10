import sqlite3

import pytest

from infrastructure.persistence.spotify_likes_store import SpotifyLikesStore


@pytest.mark.asyncio
async def test_existing_enabled_settings_gain_original_enabled_timestamp(tmp_path):
    db_path = tmp_path / "library.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE spotify_likes_settings (
            user_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            include_existing INTEGER NOT NULL DEFAULT 0,
            initialized INTEGER NOT NULL DEFAULT 0,
            requires_reconnect INTEGER NOT NULL DEFAULT 0,
            last_sync_at TEXT,
            last_error TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO spotify_likes_settings (
            user_id, enabled, include_existing, initialized,
            requires_reconnect, updated_at
        ) VALUES (?, 1, 0, 0, 1, ?)
        """,
        ("user-1", "2026-08-10T10:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    store = SpotifyLikesStore(db_path)

    settings = await store.get_settings("user-1")
    assert settings.enabled_at == "2026-08-10T10:00:00+00:00"


@pytest.mark.asyncio
async def test_enabling_sync_records_timestamp_once(tmp_path):
    db_path = tmp_path / "library.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE auth_users (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO auth_users (id) VALUES ('user-1')")
    conn.commit()
    conn.close()
    store = SpotifyLikesStore(db_path)

    await store.update_settings("user-1", enabled=True)
    enabled_at = (await store.get_settings("user-1")).enabled_at
    await store.update_settings("user-1", requires_reconnect=True)

    assert enabled_at is not None
    assert (await store.get_settings("user-1")).enabled_at == enabled_at
