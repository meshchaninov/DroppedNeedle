"""Immediate public-YouTube provisional acquisition.

This is intentionally not a normal ``SourceStrategy``: YouTube is a temporary
availability layer and must never participate in source priority or upgrade searches.
The regular P2P task runs concurrently with ``origin='youtube_upgrade'`` and may replace
the provisional file through the existing verified, recycle-backed import path.
"""

import asyncio
import logging
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rapidfuzz import fuzz

from core.exceptions import (
    PermissionDeniedError,
    ResourceNotFoundError,
    ValidationError,
)
from core.task_registry import TaskRegistry
from infrastructure.queue.priority_queue import RequestPriority
from models.download_manifest import DownloadManifest, ExpectedTrack
from services.native.acquisition.status import DownloadStatus

if TYPE_CHECKING:
    from infrastructure.persistence.download_store import DownloadStore
    from infrastructure.sse_publisher import SSEPublisher
    from repositories.yt_dlp_repository import YtDlpCandidate, YtDlpRepository
    from services.album_service import AlbumService
    from services.native.file_processor import FileProcessor
    from services.preferences_service import PreferencesService

logger = logging.getLogger(__name__)

_OFF_VERSION_TERMS = (
    "live",
    "cover",
    "karaoke",
    "nightcore",
    "sped up",
    "slowed",
    "remix",
    "acoustic",
    "instrumental",
)


def _safe_stem(disc: int, track: int, artist: str, title: str) -> str:
    value = f"d{disc:02d}-t{track:02d} {artist} - {title}"
    value = re.sub(r"[^\w .()\[\]-]+", "_", value, flags=re.UNICODE)
    return value.strip(" .")[:160] or f"d{disc:02d}-t{track:02d}"


class YouTubeProvisionalService:
    def __init__(
        self,
        *,
        repository: "YtDlpRepository",
        store: "DownloadStore",
        album_service: "AlbumService",
        file_processor: "FileProcessor",
        preferences: "PreferencesService",
        event_bus: "SSEPublisher",
        staging_path: Path,
        naming_template: str,
        request_history: Any | None = None,
        on_import_callback: Any | None = None,
    ) -> None:
        self._repo = repository
        self._store = store
        self._album = album_service
        self._processor = file_processor
        self._preferences = preferences
        self._bus = event_bus
        self._staging = Path(staging_path)
        self._naming_template = naming_template
        self._request_history = request_history
        self._on_import = on_import_callback
        self._active: dict[str, asyncio.Task[Any]] = {}

    def is_enabled(self) -> bool:
        return self._preferences.get_download_policy().youtube_provisional_enabled

    async def dispatch_album(
        self,
        *,
        user_id: str,
        release_group_mbid: str,
        artist_name: str,
        album_title: str,
        year: int | None = None,
        artist_mbid: str | None = None,
        release_mbid: str | None = None,
        track_count: int | None = None,
    ) -> str | None:
        if not self.is_enabled():
            return None
        existing = await self._store.get_active_task_for_source_target(
            source="youtube",
            user_id=user_id,
            release_group_mbid=release_group_mbid,
            recording_mbid=None,
        )
        if existing is not None:
            return existing.id
        task = await self._store.create_task(
            user_id=user_id,
            download_type="album",
            release_group_mbid=release_group_mbid,
            release_mbid=release_mbid,
            artist_mbid=artist_mbid,
            artist_name=artist_name,
            album_title=album_title,
            year=year,
            track_count=track_count,
            download_client="yt-dlp",
            source="youtube",
            origin="youtube_provisional",
        )
        self._schedule(task.id)
        return task.id

    async def dispatch_track(
        self,
        *,
        user_id: str,
        recording_mbid: str,
        artist_name: str,
        track_title: str,
        album_title: str | None,
        duration_seconds: int | None,
        release_group_mbid: str | None,
        artist_mbid: str | None = None,
        release_mbid: str | None = None,
    ) -> str | None:
        if not self.is_enabled() or not release_group_mbid:
            return None
        existing = await self._store.get_active_task_for_source_target(
            source="youtube",
            user_id=user_id,
            release_group_mbid=release_group_mbid,
            recording_mbid=recording_mbid,
        )
        if existing is not None:
            return existing.id
        task = await self._store.create_task(
            user_id=user_id,
            download_type="track",
            release_group_mbid=release_group_mbid,
            release_mbid=release_mbid,
            recording_mbid=recording_mbid,
            artist_mbid=artist_mbid,
            artist_name=artist_name,
            album_title=album_title or "Unknown Album",
            track_title=track_title,
            track_duration_seconds=duration_seconds,
            track_count=1,
            download_client="yt-dlp",
            source="youtube",
            origin="youtube_provisional",
        )
        self._schedule(task.id)
        return task.id

    def _schedule(self, task_id: str) -> None:
        if task_id in self._active and not self._active[task_id].done():
            return
        registry = TaskRegistry.get_instance()
        name = f"youtube-provisional-{task_id}"
        if registry.is_running(name):
            return
        handle = asyncio.create_task(self._run_safely(task_id))
        self._active[task_id] = handle
        handle.add_done_callback(lambda _t, key=task_id: self._active.pop(key, None))
        registry.register(name, handle)

    async def startup_resume(self) -> None:
        for task in await self._store.list_active_tasks_for_source("youtube"):
            self._schedule(task.id)

    async def cancel(self, task_id: str, user_id: str, user_role: str) -> None:
        task = await self._store.get_task(task_id)
        if task is None or task.source != "youtube":
            raise ResourceNotFoundError("Download task not found")
        if user_role != "admin" and task.user_id != user_id:
            raise PermissionDeniedError("Cannot cancel another user's download")
        await TaskRegistry.get_instance().cancel(f"youtube-provisional-{task_id}")
        await self._store.update_status(
            task_id, DownloadStatus.CANCELLED, cancelled_at=time.time()
        )
        await self._bus.publish(
            f"download:{task_id}", "complete", {"status": DownloadStatus.CANCELLED}
        )

    async def retry(self, task_id: str, user_id: str, user_role: str) -> str:
        task = await self._store.get_task(task_id)
        if task is None or task.source != "youtube":
            raise ResourceNotFoundError("Download task not found")
        if user_role != "admin" and task.user_id != user_id:
            raise PermissionDeniedError("Cannot retry another user's download")
        if task.status not in (
            DownloadStatus.FAILED,
            DownloadStatus.PARTIAL,
            DownloadStatus.CANCELLED,
        ):
            raise ValidationError(
                "Only failed, partial or cancelled downloads can be retried"
            )
        await self._store.update_status(
            task_id,
            DownloadStatus.QUEUED,
            error_message=None,
            completed_at=None,
            cancelled_at=None,
            progress_percent=0,
            files_completed=0,
            files_failed=0,
        )
        self._schedule(task_id)
        return task_id

    async def _run_safely(self, task_id: str) -> None:
        try:
            await self._run(task_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("youtube.provisional_failed", extra={"task_id": task_id})
            await self._store.update_status(
                task_id,
                DownloadStatus.FAILED,
                error_message="temporary YouTube download failed",
                completed_at=time.time(),
            )
            await self._bus.publish(
                f"download:{task_id}",
                "complete",
                {
                    "status": DownloadStatus.FAILED,
                    "error": "temporary YouTube download failed",
                },
            )
        finally:
            await asyncio.to_thread(
                shutil.rmtree, self._staging / task_id, ignore_errors=True
            )

    async def _run(self, task_id: str) -> None:
        task = await self._store.get_task(task_id)
        if task is None:
            return
        available, _version = self._repo.availability()
        if not available:
            raise RuntimeError("yt-dlp unavailable")
        tracks = await self._expected_tracks(task)
        if not tracks:
            raise RuntimeError("MusicBrainz track list unavailable")
        await self._store.update_status(
            task_id,
            DownloadStatus.DOWNLOADING,
            started_at=time.time(),
            files_total=len(tracks),
            quality_format="mp3",
            quality_bitrate=320,
        )
        await self._bus.publish(
            f"download:{task_id}", "status", {"status": DownloadStatus.DOWNLOADING}
        )

        policy = self._preferences.get_download_policy()
        semaphore = asyncio.Semaphore(policy.youtube_max_concurrent_downloads)
        counter_lock = asyncio.Lock()
        succeeded: list[str] = []
        failed = 0

        async def acquire(track: ExpectedTrack) -> None:
            nonlocal failed
            try:
                async with semaphore:
                    path = await self._acquire_track(task, track)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one unavailable video must not fail an album
                logger.warning(
                    "youtube.provisional_track_failed",
                    exc_info=True,
                    extra={
                        "task_id": task_id,
                        "recording_mbid": track.recording_mbid,
                    },
                )
                path = None
            async with counter_lock:
                if path is None:
                    failed += 1
                else:
                    succeeded.append(path)
                done = len(succeeded) + failed
                await self._store.update_progress(
                    task_id,
                    bytes_downloaded=0,
                    files_completed=len(succeeded),
                    progress_percent=round(done * 100 / len(tracks)),
                )
                await self._bus.publish(
                    f"download:{task_id}",
                    "progress",
                    {
                        "files_completed": len(succeeded),
                        "files_failed": failed,
                        "progress_percent": round(done * 100 / len(tracks)),
                    },
                )

        await asyncio.gather(*(acquire(track) for track in tracks))
        if succeeded:
            await self._store.set_final_path(task_id, str(Path(succeeded[0]).parent))
        status = (
            DownloadStatus.COMPLETED
            if succeeded and not failed
            else DownloadStatus.PARTIAL
            if succeeded
            else DownloadStatus.FAILED
        )
        await self._store.update_status(
            task_id,
            status,
            completed_at=time.time(),
            files_completed=len(succeeded),
            files_failed=failed,
            progress_percent=100,
            error_message=None
            if succeeded
            else "no suitable public YouTube track found",
        )
        if succeeded:
            await self._sync_request_after_import(task, status)
        await self._bus.publish(f"download:{task_id}", "complete", {"status": status})

    async def _sync_request_after_import(self, task, status: str) -> None:  # noqa: ANN001
        """Make the provisional copy visible even when the quality task failed first."""
        if self._request_history is None or not task.release_group_mbid:
            return
        try:
            record = await self._request_history.async_get_record(
                task.release_group_mbid
            )
            if record is None or not getattr(record, "download_task_id", None):
                return
            quality_task = await self._store.get_task(record.download_task_id)
            if (
                quality_task is None
                or quality_task.origin != "youtube_upgrade"
                or quality_task.user_id != task.user_id
            ):
                return
            request_status = (
                "imported" if status == DownloadStatus.COMPLETED else "incomplete"
            )
            completed_at = (
                datetime.now(timezone.utc).isoformat()
                if request_status == "imported"
                else None
            )
            await self._request_history.async_update_status(
                record.musicbrainz_id,
                request_status,
                completed_at=completed_at,
            )
            if self._on_import is not None:
                await self._on_import(record)
        except Exception:  # noqa: BLE001 - cache/request sync cannot undo an import
            logger.warning(
                "youtube.provisional_request_sync_failed",
                exc_info=True,
                extra={"task_id": task.id},
            )

    async def _expected_tracks(self, task) -> list[ExpectedTrack]:  # noqa: ANN001
        info = await self._album.get_album_tracks_info(
            task.release_group_mbid, priority=RequestPriority.USER_INITIATED
        )
        available = list(info.tracks or [])
        if task.download_type == "track":
            match = next(
                (
                    track
                    for track in available
                    if track.recording_id == task.recording_mbid
                ),
                None,
            )
            return [
                ExpectedTrack(
                    track_number=match.position if match else task.track_number or 1,
                    disc_number=match.disc_number if match else task.disc_number or 1,
                    duration_seconds=(
                        match.length / 1000.0
                        if match and match.length
                        else task.track_duration_seconds
                    ),
                    recording_mbid=task.recording_mbid,
                    title=task.track_title,
                )
            ]
        return [
            ExpectedTrack(
                track_number=track.position,
                disc_number=track.disc_number,
                duration_seconds=(track.length / 1000.0) if track.length else None,
                recording_mbid=track.recording_id,
                title=track.title,
            )
            for track in available
        ]

    async def _acquire_track(self, task, track: ExpectedTrack) -> str | None:  # noqa: ANN001
        title = track.title or task.track_title or ""
        query = f"{task.artist_name} {title} official audio"
        candidates = await self._repo.search(query, limit=5)
        candidate = self._pick_candidate(
            candidates,
            artist=task.artist_name,
            title=title,
            duration=track.duration_seconds,
        )
        if candidate is None:
            return None
        track_dir = (
            self._staging
            / task.id
            / f"d{track.disc_number:02d}-t{track.track_number:02d}"
        )
        stem = _safe_stem(
            track.disc_number, track.track_number, task.artist_name, title
        )
        source = await self._repo.download_mp3(candidate.video_id, track_dir, stem)
        manifest = DownloadManifest(
            task_id=task.id,
            release_group_mbid=task.release_group_mbid,
            release_mbid=task.release_mbid,
            artist_mbid=task.artist_mbid,
            artist_name=task.artist_name,
            album_title=task.album_title,
            year=task.year,
            naming_template=self._naming_template,
            target_files=[],
            expected_tracks=[track],
            is_track=True,
            origin="youtube_provisional",
            acquisition_source="youtube",
        )
        result = await self._processor.process_downloaded_folder(manifest, [source])
        return result.succeeded[0] if result.succeeded else None

    @staticmethod
    def _pick_candidate(
        candidates: list["YtDlpCandidate"],
        *,
        artist: str,
        title: str,
        duration: float | None,
    ) -> "YtDlpCandidate | None":
        requested = f"{artist} {title}".casefold()
        requested_terms = {term for term in _OFF_VERSION_TERMS if term in requested}
        ranked: list[tuple[float, YtDlpCandidate]] = []
        for candidate in candidates:
            text = f"{candidate.title} {candidate.channel}".casefold()
            if any(
                term in text and term not in requested_terms
                for term in _OFF_VERSION_TERMS
            ):
                continue
            if duration is not None:
                if candidate.duration_seconds is None:
                    continue
                tolerance = max(10.0, duration * 0.08)
                delta = abs(candidate.duration_seconds - duration)
                if delta > tolerance:
                    continue
                duration_score = 1.0 - delta / tolerance
            else:
                duration_score = 0.5
            title_score = (
                fuzz.token_set_ratio(title.casefold(), candidate.title.casefold())
                / 100.0
            )
            artist_score = fuzz.token_set_ratio(artist.casefold(), text) / 100.0
            official_bonus = 0.05 if ("topic" in text or "official" in text) else 0.0
            score = (
                0.55 * title_score
                + 0.2 * artist_score
                + 0.25 * duration_score
                + official_bonus
            )
            if title_score >= 0.72 and artist_score >= 0.55 and score >= 0.72:
                ranked.append((score, candidate))
        return max(ranked, key=lambda item: item[0])[1] if ranked else None
