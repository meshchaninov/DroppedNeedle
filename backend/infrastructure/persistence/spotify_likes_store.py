"""Persistence for per-user Spotify liked-track auto-request state."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import msgspec


class SpotifyLikesSettings(msgspec.Struct, frozen=True):
    user_id: str
    enabled: bool = False
    include_existing: bool = False
    initialized: bool = False
    requires_reconnect: bool = False
    enabled_at: str | None = None
    last_sync_at: str | None = None
    last_error: str | None = None
    updated_at: str = ""


class SpotifyLikedTrack(msgspec.Struct, frozen=True):
    user_id: str
    spotify_track_id: str
    added_at: str
    artist_name: str
    track_title: str
    album_title: str | None
    duration_seconds: int | None
    isrc: str | None
    status: str
    recording_mbid: str | None = None
    release_group_mbid: str | None = None
    request_task_id: str | None = None
    error: str | None = None


class SpotifyLikesStore:
    def __init__(self, db_path: Path, write_lock: threading.Lock | None = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = write_lock or threading.Lock()
        with self._write_lock:
            self._ensure_tables()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_tables(self) -> None:
        conn = self._connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS spotify_likes_settings (
                    user_id TEXT PRIMARY KEY REFERENCES auth_users(id) ON DELETE CASCADE,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    include_existing INTEGER NOT NULL DEFAULT 0,
                    initialized INTEGER NOT NULL DEFAULT 0,
                    requires_reconnect INTEGER NOT NULL DEFAULT 0,
                    enabled_at TEXT,
                    last_sync_at TEXT,
                    last_error TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS spotify_liked_tracks (
                    user_id TEXT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
                    spotify_track_id TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    track_title TEXT NOT NULL,
                    album_title TEXT,
                    duration_seconds INTEGER,
                    isrc TEXT,
                    status TEXT NOT NULL,
                    recording_mbid TEXT,
                    release_group_mbid TEXT,
                    request_task_id TEXT,
                    error TEXT,
                    processed_at TEXT,
                    PRIMARY KEY (user_id, spotify_track_id)
                );
                CREATE INDEX IF NOT EXISTS idx_spotify_liked_tracks_pending
                    ON spotify_liked_tracks(user_id, status, added_at);
                """)
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(spotify_likes_settings)")
            }
            if "enabled_at" not in columns:
                conn.execute(
                    "ALTER TABLE spotify_likes_settings ADD COLUMN enabled_at TEXT"
                )
                conn.execute(
                    """
                    UPDATE spotify_likes_settings
                    SET enabled_at = updated_at
                    WHERE enabled = 1 AND enabled_at IS NULL
                    """
                )
            conn.commit()
        finally:
            conn.close()

    def _execute(self, operation, write: bool):
        if write:
            with self._write_lock:
                conn = self._connect()
                try:
                    result = operation(conn)
                    conn.commit()
                    return result
                finally:
                    conn.close()
        conn = self._connect()
        try:
            return operation(conn)
        finally:
            conn.close()

    async def _read(self, operation):
        return await asyncio.to_thread(self._execute, operation, False)

    async def _write(self, operation):
        return await asyncio.to_thread(self._execute, operation, True)

    @staticmethod
    def _settings(row: sqlite3.Row | None, user_id: str) -> SpotifyLikesSettings:
        if row is None:
            return SpotifyLikesSettings(user_id=user_id)
        return SpotifyLikesSettings(
            user_id=row["user_id"],
            enabled=bool(row["enabled"]),
            include_existing=bool(row["include_existing"]),
            initialized=bool(row["initialized"]),
            requires_reconnect=bool(row["requires_reconnect"]),
            enabled_at=row["enabled_at"],
            last_sync_at=row["last_sync_at"],
            last_error=row["last_error"],
            updated_at=row["updated_at"],
        )

    async def get_settings(self, user_id: str) -> SpotifyLikesSettings:
        def operation(conn: sqlite3.Connection):
            return conn.execute(
                "SELECT * FROM spotify_likes_settings WHERE user_id = ?", (user_id,)
            ).fetchone()

        return self._settings(await self._read(operation), user_id)

    async def update_settings(
        self,
        user_id: str,
        *,
        enabled: bool | None = None,
        include_existing: bool | None = None,
        initialized: bool | None = None,
        requires_reconnect: bool | None = None,
        last_sync_at: str | None = None,
        last_error: str | None = None,
        clear_error: bool = False,
    ) -> None:
        current = await self.get_settings(user_id)
        now = datetime.now(timezone.utc).isoformat()
        next_enabled = current.enabled if enabled is None else enabled
        if next_enabled and not current.enabled:
            enabled_at = now
        elif next_enabled:
            enabled_at = current.enabled_at
        else:
            enabled_at = None
        values = SpotifyLikesSettings(
            user_id=user_id,
            enabled=next_enabled,
            include_existing=(
                current.include_existing if include_existing is None else include_existing
            ),
            initialized=current.initialized if initialized is None else initialized,
            requires_reconnect=(
                current.requires_reconnect if requires_reconnect is None else requires_reconnect
            ),
            enabled_at=enabled_at,
            last_sync_at=current.last_sync_at if last_sync_at is None else last_sync_at,
            last_error=(
                None if clear_error else (current.last_error if last_error is None else last_error)
            ),
            updated_at=now,
        )

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO spotify_likes_settings (
                    user_id, enabled, include_existing, initialized,
                    requires_reconnect, enabled_at, last_sync_at, last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    include_existing = excluded.include_existing,
                    initialized = excluded.initialized,
                    requires_reconnect = excluded.requires_reconnect,
                    enabled_at = excluded.enabled_at,
                    last_sync_at = excluded.last_sync_at,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (
                    values.user_id,
                    int(values.enabled),
                    int(values.include_existing),
                    int(values.initialized),
                    int(values.requires_reconnect),
                    values.enabled_at,
                    values.last_sync_at,
                    values.last_error,
                    values.updated_at,
                ),
            )

        await self._write(operation)

    async def list_enabled_user_ids(self) -> list[str]:
        def operation(conn: sqlite3.Connection):
            return conn.execute(
                "SELECT user_id FROM spotify_likes_settings WHERE enabled = 1"
            ).fetchall()

        rows = await self._read(operation)
        return [row["user_id"] for row in rows]

    async def add_tracks(self, user_id: str, tracks: list[dict], status: str) -> int:
        if not tracks:
            return 0

        def operation(conn: sqlite3.Connection) -> int:
            inserted = 0
            for track in tracks:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO spotify_liked_tracks (
                        user_id, spotify_track_id, added_at, artist_name,
                        track_title, album_title, duration_seconds, isrc, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        track["spotify_track_id"],
                        track["added_at"],
                        track["artist_name"],
                        track["track_title"],
                        track.get("album_title"),
                        track.get("duration_seconds"),
                        track.get("isrc"),
                        status,
                    ),
                )
                inserted += cursor.rowcount
            return inserted

        return await self._write(operation)

    async def recent_track_ids(self, user_id: str, limit: int = 100) -> set[str]:
        def operation(conn: sqlite3.Connection):
            return conn.execute(
                """
                SELECT spotify_track_id FROM spotify_liked_tracks
                WHERE user_id = ? ORDER BY added_at DESC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

        return {row["spotify_track_id"] for row in await self._read(operation)}

    async def pending_tracks(self, user_id: str, limit: int = 20) -> list[SpotifyLikedTrack]:
        def operation(conn: sqlite3.Connection):
            return conn.execute(
                """
                SELECT * FROM spotify_liked_tracks
                WHERE user_id = ? AND status = 'pending'
                ORDER BY added_at ASC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

        rows = await self._read(operation)
        return [
            SpotifyLikedTrack(
                user_id=row["user_id"],
                spotify_track_id=row["spotify_track_id"],
                added_at=row["added_at"],
                artist_name=row["artist_name"],
                track_title=row["track_title"],
                album_title=row["album_title"],
                duration_seconds=row["duration_seconds"],
                isrc=row["isrc"],
                status=row["status"],
                recording_mbid=row["recording_mbid"],
                release_group_mbid=row["release_group_mbid"],
                request_task_id=row["request_task_id"],
                error=row["error"],
            )
            for row in rows
        ]

    async def finish_track(
        self,
        user_id: str,
        spotify_track_id: str,
        *,
        status: str,
        recording_mbid: str | None = None,
        release_group_mbid: str | None = None,
        request_task_id: str | None = None,
        error: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                UPDATE spotify_liked_tracks SET
                    status = ?, recording_mbid = ?, release_group_mbid = ?,
                    request_task_id = ?, error = ?, processed_at = ?
                WHERE user_id = ? AND spotify_track_id = ?
                """,
                (
                    status,
                    recording_mbid,
                    release_group_mbid,
                    request_task_id,
                    error,
                    now,
                    user_id,
                    spotify_track_id,
                ),
            )

        await self._write(operation)

    async def counts(self, user_id: str) -> dict[str, int]:
        def operation(conn: sqlite3.Connection):
            return conn.execute(
                """
                SELECT status, COUNT(*) AS count FROM spotify_liked_tracks
                WHERE user_id = ? GROUP BY status
                """,
                (user_id,),
            ).fetchall()

        return {row["status"]: row["count"] for row in await self._read(operation)}
