"""Turn newly liked Yandex Music tracks into native track requests."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from services.native.download_service import ALREADY_IN_LIBRARY
from services.spotify_likes_sync_service import (
    SpotifyLikesSyncService,
    _liked_after_enabled,
)
from services.yandex_music_client import YandexMusicAuthError

if TYPE_CHECKING:
    from infrastructure.persistence.auth_store import AuthStore
    from infrastructure.persistence.yandex_music_likes_store import (
        YandexMusicLikesSettings,
        YandexMusicLikesStore,
    )
    from repositories.musicbrainz_repository import MusicBrainzRepository
    from services.acquisition_dispatcher import AcquisitionDispatcher
    from services.per_user_client_factory import PerUserClientFactory
    from services.quota_service import QuotaService

logger = logging.getLogger(__name__)

_BATCH_SIZE = 20


class YandexMusicLikesSyncService:
    """Yandex-specific collection layered over the proven Spotify matcher."""

    def __init__(
        self,
        *,
        store: YandexMusicLikesStore,
        client_factory: PerUserClientFactory,
        musicbrainz: MusicBrainzRepository,
        acquisition: AcquisitionDispatcher,
        quota: QuotaService,
        auth: AuthStore,
    ) -> None:
        self._store = store
        self._clients = client_factory
        self._musicbrainz = musicbrainz
        self._acquisition = acquisition
        self._quota = quota
        self._auth = auth
        self._locks: dict[str, asyncio.Lock] = {}

    # Matching is provider-agnostic, so reuse the exact confidence thresholds and
    # release selection shipped with the Spotify implementation.
    _match = SpotifyLikesSyncService._match
    _match_isrc = SpotifyLikesSyncService._match_isrc
    _match_text = SpotifyLikesSyncService._match_text
    _release_for_album = staticmethod(SpotifyLikesSyncService._release_for_album)

    async def settings(self, user_id: str) -> YandexMusicLikesSettings:
        return await self._store.get_settings(user_id)

    async def update_settings(
        self, user_id: str, *, enabled: bool, include_existing: bool
    ) -> YandexMusicLikesSettings:
        await self._store.update_settings(
            user_id,
            enabled=enabled,
            include_existing=include_existing,
            clear_error=True,
        )
        return await self._store.get_settings(user_id)

    async def status(self, user_id: str) -> dict[str, Any]:
        settings = await self._store.get_settings(user_id)
        counts = await self._store.counts(user_id)
        return {
            "enabled": settings.enabled,
            "include_existing": settings.include_existing,
            "initialized": settings.initialized,
            "last_sync_at": settings.last_sync_at,
            "last_error": settings.last_error,
            "pending": counts.get("pending", 0),
            "requested": counts.get("requested", 0),
            "already_in_library": counts.get("already_in_library", 0),
            "unmatched": counts.get("unmatched", 0),
            "failed": counts.get("failed", 0),
            "ignored": counts.get("ignored", 0),
        }

    async def sync_enabled_users(self) -> None:
        for user_id in await self._store.list_enabled_user_ids():
            try:
                await self.sync_user(user_id)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Yandex Music liked-track sync failed for user %s: %s",
                    user_id,
                    exc,
                    exc_info=True,
                )
                await self._store.update_settings(user_id, last_error=str(exc))

    async def sync_user(self, user_id: str, *, force: bool = False) -> dict[str, Any]:
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            return await self._sync_user(user_id, force=force)

    async def _sync_user(self, user_id: str, *, force: bool = False) -> dict[str, Any]:
        settings = await self._store.get_settings(user_id)
        if not settings.enabled and not force:
            return await self.status(user_id)

        client = await self._clients.resolve_yandex_music(user_id)
        if client is None:
            await self._store.update_settings(
                user_id, last_error="Yandex Music account is not connected"
            )
            return await self.status(user_id)

        try:
            known_ids = await self._store.recent_track_ids(user_id)
            raw_items = await client.get_liked_tracks(stop_at_ids=known_ids)
        except YandexMusicAuthError:
            await self._store.update_settings(
                user_id, last_error="Reconnect Yandex Music with a valid token"
            )
            return await self.status(user_id)

        tracks = [self._yandex_track(item) for item in raw_items]
        tracks = [track for track in tracks if track is not None]
        if settings.initialized or settings.include_existing:
            await self._store.add_tracks(user_id, tracks, "pending")
        else:
            newly_liked = [
                track
                for track in tracks
                if _liked_after_enabled(track["added_at"], settings.enabled_at)
            ]
            baseline = [track for track in tracks if track not in newly_liked]
            if baseline:
                await self._store.add_tracks(user_id, baseline, "ignored")
            if newly_liked:
                await self._store.add_tracks(user_id, newly_liked, "pending")
        await self._store.update_settings(user_id, initialized=True, clear_error=True)

        user = await self._auth.get_user_by_id(user_id)
        if user is None:
            return await self.status(user_id)

        for track in await self._store.pending_tracks(user_id, limit=_BATCH_SIZE):
            try:
                await self._quota.check_request_quota(user_id, user.role)
                match = await self._match(track)
                if match is None:
                    await self._store.finish_track(
                        user_id,
                        track.yandex_track_id,
                        status="unmatched",
                        error="No confident MusicBrainz recording match",
                    )
                    continue
                recording_mbid, release_group_mbid, release_mbid = match
                task_id = await self._acquisition.request_track(
                    user_id=user_id,
                    recording_mbid=recording_mbid,
                    artist_name=track.artist_name,
                    track_title=track.track_title,
                    album_title=track.album_title,
                    duration_seconds=track.duration_seconds,
                    release_group_mbid=release_group_mbid,
                    origin="yandex_music_liked",
                    release_mbid=release_mbid,
                )
                status = "already_in_library" if task_id == ALREADY_IN_LIBRARY else "requested"
                await self._store.finish_track(
                    user_id,
                    track.yandex_track_id,
                    status=status,
                    recording_mbid=recording_mbid,
                    release_group_mbid=release_group_mbid,
                    request_task_id=None if task_id == ALREADY_IN_LIBRARY else task_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Yandex Music liked track %s failed: %s", track.yandex_track_id, exc)
                await self._store.finish_track(
                    user_id, track.yandex_track_id, status="failed", error=str(exc)
                )

        await self._store.update_settings(
            user_id,
            last_sync_at=datetime.now(timezone.utc).isoformat(),
            clear_error=True,
        )
        return await self.status(user_id)

    @staticmethod
    def _yandex_track(item: dict) -> dict[str, Any] | None:
        track = item.get("track") or {}
        track_id = track.get("id")
        title = track.get("title")
        artists = ", ".join(
            artist.get("name", "") for artist in (track.get("artists") or []) if artist.get("name")
        )
        if not track_id or not title or not artists:
            return None
        albums = track.get("albums") or []
        album = albums[0] if albums else {}
        duration_ms = track.get("durationMs") or track.get("duration_ms")
        metadata = track.get("metaData") or track.get("meta_data") or {}
        return {
            "yandex_track_id": str(track_id),
            "added_at": item.get("added_at") or "",
            "artist_name": artists,
            "track_title": title,
            "album_title": album.get("title") or None,
            "duration_seconds": round(duration_ms / 1000) if duration_ms else None,
            "isrc": metadata.get("isrc") or track.get("isrc"),
        }
