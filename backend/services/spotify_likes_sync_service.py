"""Turn newly liked Spotify tracks into native DroppedNeedle track requests."""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

from infrastructure.queue.priority_queue import RequestPriority
from repositories.musicbrainz_album import _pick_best_release_group
from repositories.musicbrainz_base import extract_artist_name, mb_api_get
from services.native.download_service import ALREADY_IN_LIBRARY
from services.spotify_client import SpotifyLibraryScopeError

if TYPE_CHECKING:
    from infrastructure.persistence.auth_store import AuthStore
    from infrastructure.persistence.spotify_likes_store import (
        SpotifyLikedTrack,
        SpotifyLikesSettings,
        SpotifyLikesStore,
    )
    from repositories.musicbrainz_repository import MusicBrainzRepository
    from services.acquisition_dispatcher import AcquisitionDispatcher
    from services.per_user_client_factory import PerUserClientFactory
    from services.quota_service import QuotaService

logger = logging.getLogger(__name__)

_BATCH_SIZE = 20


def _normalized(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "").casefold()
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def _similarity(left: str | None, right: str | None) -> float:
    return SequenceMatcher(None, _normalized(left), _normalized(right)).ratio()


class SpotifyLikesSyncService:
    def __init__(
        self,
        *,
        store: SpotifyLikesStore,
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

    async def settings(self, user_id: str) -> SpotifyLikesSettings:
        return await self._store.get_settings(user_id)

    async def update_settings(
        self, user_id: str, *, enabled: bool, include_existing: bool
    ) -> SpotifyLikesSettings:
        client = await self._clients.resolve_spotify(user_id)
        requires_reconnect = bool(client and not client.has_library_scope)
        await self._store.update_settings(
            user_id,
            enabled=enabled,
            include_existing=include_existing,
            requires_reconnect=requires_reconnect,
            clear_error=not requires_reconnect,
            last_error=(
                "Reconnect Spotify to grant access to liked tracks" if requires_reconnect else None
            ),
        )
        return await self._store.get_settings(user_id)

    async def status(self, user_id: str) -> dict[str, Any]:
        settings = await self._store.get_settings(user_id)
        counts = await self._store.counts(user_id)
        return {
            "enabled": settings.enabled,
            "include_existing": settings.include_existing,
            "initialized": settings.initialized,
            "requires_reconnect": settings.requires_reconnect,
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
                    "Spotify liked-track sync failed for user %s: %s",
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

        client = await self._clients.resolve_spotify(user_id)
        if client is None:
            await self._store.update_settings(
                user_id, last_error="Spotify account is not connected"
            )
            return await self.status(user_id)
        if not client.has_library_scope:
            await self._store.update_settings(
                user_id,
                requires_reconnect=True,
                last_error="Reconnect Spotify to grant access to liked tracks",
            )
            return await self.status(user_id)

        try:
            known_ids = await self._store.recent_track_ids(user_id)
            raw_items = await client.get_saved_tracks(stop_at_ids=known_ids)
        except SpotifyLibraryScopeError:
            await self._store.update_settings(
                user_id,
                requires_reconnect=True,
                last_error="Reconnect Spotify to grant access to liked tracks",
            )
            return await self.status(user_id)

        tracks = [self._spotify_track(item) for item in raw_items]
        tracks = [track for track in tracks if track is not None]
        initial_status = (
            "pending" if settings.initialized or settings.include_existing else "ignored"
        )
        await self._store.add_tracks(user_id, tracks, initial_status)
        await self._store.update_settings(
            user_id,
            initialized=True,
            requires_reconnect=False,
            clear_error=True,
        )

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
                        track.spotify_track_id,
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
                    origin="spotify_liked",
                    release_mbid=release_mbid,
                )
                status = "already_in_library" if task_id == ALREADY_IN_LIBRARY else "requested"
                await self._store.finish_track(
                    user_id,
                    track.spotify_track_id,
                    status=status,
                    recording_mbid=recording_mbid,
                    release_group_mbid=release_group_mbid,
                    request_task_id=None if task_id == ALREADY_IN_LIBRARY else task_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Spotify liked track %s failed: %s", track.spotify_track_id, exc)
                await self._store.finish_track(
                    user_id,
                    track.spotify_track_id,
                    status="failed",
                    error=str(exc),
                )

        from datetime import datetime, timezone

        await self._store.update_settings(
            user_id,
            last_sync_at=datetime.now(timezone.utc).isoformat(),
            clear_error=True,
        )
        return await self.status(user_id)

    @staticmethod
    def _spotify_track(item: dict) -> dict[str, Any] | None:
        track = item.get("track") or {}
        spotify_id = track.get("id")
        title = track.get("name")
        artists = ", ".join(
            artist.get("name", "") for artist in (track.get("artists") or []) if artist.get("name")
        )
        if not spotify_id or not title or not artists:
            return None
        album = track.get("album") or {}
        duration_ms = track.get("duration_ms")
        return {
            "spotify_track_id": spotify_id,
            "added_at": item.get("added_at") or "",
            "artist_name": artists,
            "track_title": title,
            "album_title": album.get("name") or None,
            "duration_seconds": round(duration_ms / 1000) if duration_ms else None,
            "isrc": (track.get("external_ids") or {}).get("isrc"),
        }

    async def _match(self, track: SpotifyLikedTrack) -> tuple[str, str | None, str | None] | None:
        if track.isrc:
            match = await self._match_isrc(track)
            if match is not None:
                return match
        return await self._match_text(track)

    async def _match_isrc(
        self, track: SpotifyLikedTrack
    ) -> tuple[str, str | None, str | None] | None:
        try:
            data = await mb_api_get(f"/isrc/{track.isrc}", priority=RequestPriority.BACKGROUND_SYNC)
        except Exception:  # noqa: BLE001
            return None
        recordings = data.get("recordings") or []
        if isinstance(recordings, dict):
            recordings = [recordings]
        best: tuple[float, dict] | None = None
        for recording in recordings:
            if not recording.get("id"):
                continue
            confidence = _similarity(track.track_title, recording.get("title"))
            artist = extract_artist_name(recording)
            if artist:
                confidence = confidence * 0.7 + _similarity(track.artist_name, artist) * 0.3
            if best is None or confidence > best[0]:
                best = (confidence, recording)
        if best is None or best[0] < 0.78:
            return None
        recording = best[1]
        release_group_mbid, release_mbid = self._release_for_album(
            recording.get("releases") or [], track.album_title
        )
        if not release_group_mbid:
            release_group_mbid = await self._musicbrainz.resolve_recording_to_release_group(
                recording["id"]
            )
        return recording["id"], release_group_mbid, release_mbid

    async def _match_text(
        self, track: SpotifyLikedTrack
    ) -> tuple[str, str | None, str | None] | None:
        candidates = await self._musicbrainz.search_recordings(
            track.artist_name,
            track.track_title,
            priority=RequestPriority.BACKGROUND_SYNC,
        )
        ranked: list[tuple[float, Any, Any]] = []
        for candidate in candidates:
            title_score = _similarity(track.track_title, candidate.title)
            artist_score = _similarity(track.artist_name, candidate.artist)
            if candidate.score < 90 or title_score < 0.9 or artist_score < 0.8:
                continue
            groups = candidate.release_groups
            group = max(
                groups,
                key=lambda value: _similarity(track.album_title, value.release_group_title),
                default=None,
            )
            album_score = (
                _similarity(track.album_title, group.release_group_title)
                if group and track.album_title
                else 0.8
            )
            ranked.append(
                (title_score * 0.5 + artist_score * 0.3 + album_score * 0.2, candidate, group)
            )
        if not ranked:
            return None
        _confidence, candidate, group = max(ranked, key=lambda item: item[0])
        return (
            candidate.recording_mbid,
            group.release_group_mbid if group else None,
            group.release_mbid if group else None,
        )

    @staticmethod
    def _release_for_album(
        releases: list[dict], album_title: str | None
    ) -> tuple[str | None, str | None]:
        if album_title:
            matching = [
                release
                for release in releases
                if _similarity(album_title, (release.get("release-group") or {}).get("title"))
                >= 0.82
            ]
            if matching:
                picked = max(
                    matching,
                    key=lambda release: _similarity(
                        album_title, (release.get("release-group") or {}).get("title")
                    ),
                )
                return (release_group := picked.get("release-group") or {}).get("id"), picked.get(
                    "id"
                )
        best = _pick_best_release_group(releases)
        return (best[0] if best else None), None
