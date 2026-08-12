"""Public YouTube search/download adapter backed by yt-dlp.

All blocking extractor and FFmpeg work stays behind this repository and runs in a
worker thread.  The service passes only fixed public video URLs and app-owned output
paths; no cookies, browser profiles, shell commands, or user-defined templates are used.
"""

import asyncio
import re
from pathlib import Path

from infrastructure.msgspec_fastapi import AppStruct


class YtDlpCandidate(AppStruct, frozen=True):
    video_id: str
    title: str
    duration_seconds: float | None = None
    channel: str = ""


class YtDlpRepository:
    def availability(self) -> tuple[bool, str]:
        try:
            import yt_dlp  # type: ignore[import-not-found]
        except ImportError:
            return False, "yt-dlp is not installed"
        return True, str(getattr(yt_dlp.version, "__version__", "available"))

    async def search(self, query: str, *, limit: int = 5) -> list[YtDlpCandidate]:
        return await asyncio.to_thread(self._search_sync, query, limit)

    @staticmethod
    def _search_sync(query: str, limit: int) -> list[YtDlpCandidate]:
        import yt_dlp  # type: ignore[import-not-found]

        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 20,
            "extractor_retries": 2,
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            payload = ydl.extract_info(
                f"ytsearch{max(1, min(limit, 10))}:{query}", download=False
            )
        entries = payload.get("entries") if isinstance(payload, dict) else None
        results: list[YtDlpCandidate] = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            video_id = str(entry.get("id") or "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                continue
            duration = entry.get("duration")
            results.append(
                YtDlpCandidate(
                    video_id=video_id,
                    title=str(entry.get("title") or ""),
                    duration_seconds=float(duration) if duration is not None else None,
                    channel=str(entry.get("channel") or entry.get("uploader") or ""),
                )
            )
        return results

    async def download_mp3(self, video_id: str, destination: Path, stem: str) -> Path:
        return await asyncio.to_thread(
            self._download_mp3_sync, video_id, destination, stem
        )

    @staticmethod
    def _download_mp3_sync(video_id: str, destination: Path, stem: str) -> Path:
        import yt_dlp  # type: ignore[import-not-found]

        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError("invalid YouTube video id")
        destination.mkdir(parents=True, exist_ok=True)
        output = destination / f"{stem}.%(ext)s"
        options = {
            "format": "bestaudio/best",
            "outtmpl": str(output),
            "paths": {"home": str(destination)},
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "max_filesize": 500 * 1024 * 1024,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "320",
                }
            ],
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=True
            )
        final = destination / f"{stem}.mp3"
        if not final.is_file():
            raise RuntimeError("yt-dlp did not produce an MP3 file")
        return final
