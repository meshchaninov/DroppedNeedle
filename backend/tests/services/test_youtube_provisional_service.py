from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from repositories.yt_dlp_repository import YtDlpCandidate
from services.native.acquisition.status import DownloadStatus
from services.native.youtube_provisional_service import YouTubeProvisionalService
from repositories.yt_dlp_repository import YtDlpRepository


class _FakeYoutubeDL:
    def __init__(self, options, *, payload=None, create_output=False):
        self.options = options
        self.payload = payload or {}
        self.create_output = create_output
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def extract_info(self, url, download):
        self.calls.append((url, download))
        if self.create_output:
            Path(self.options["outtmpl"].replace("%(ext)s", "mp3")).write_bytes(b"mp3")
        return self.payload


def test_candidate_selection_prefers_exact_public_audio_and_rejects_live_versions():
    candidates = [
        YtDlpCandidate(
            video_id="aaaaaaaaaaa",
            title="Artist - Track (Live)",
            duration_seconds=201,
            channel="Artist",
        ),
        YtDlpCandidate(
            video_id="bbbbbbbbbbb",
            title="Artist - Track",
            duration_seconds=200,
            channel="Artist - Topic",
        ),
        YtDlpCandidate(
            video_id="ccccccccccc",
            title="Artist - Track",
            duration_seconds=280,
            channel="Artist - Topic",
        ),
    ]

    selected = YouTubeProvisionalService._pick_candidate(
        candidates, artist="Artist", title="Track", duration=200
    )

    assert selected is not None
    assert selected.video_id == "bbbbbbbbbbb"


def test_ytdlp_adapter_uses_public_search_without_cookie_sources(monkeypatch):
    fake = _FakeYoutubeDL(
        {},
        payload={
            "entries": [
                {
                    "id": "aaaaaaaaaaa",
                    "title": "Artist - Track",
                    "duration": 200,
                    "channel": "Artist - Topic",
                }
            ]
        },
    )
    monkeypatch.setitem(
        sys.modules,
        "yt_dlp",
        SimpleNamespace(YoutubeDL=lambda options: setattr(fake, "options", options) or fake),
    )

    results = YtDlpRepository._search_sync("Artist Track", 5)

    assert results[0].video_id == "aaaaaaaaaaa"
    assert fake.calls == [("ytsearch5:Artist Track", False)]
    assert "cookiefile" not in fake.options
    assert "cookiesfrombrowser" not in fake.options


def test_ytdlp_adapter_downloads_best_audio_and_transcodes_mp3_320(
    monkeypatch, tmp_path: Path,
):
    fake = _FakeYoutubeDL({}, create_output=True)
    monkeypatch.setitem(
        sys.modules,
        "yt_dlp",
        SimpleNamespace(YoutubeDL=lambda options: setattr(fake, "options", options) or fake),
    )

    result = YtDlpRepository._download_mp3_sync(
        "aaaaaaaaaaa", tmp_path, "safe-track"
    )

    assert result == tmp_path / "safe-track.mp3"
    assert fake.calls == [("https://www.youtube.com/watch?v=aaaaaaaaaaa", True)]
    assert fake.options["format"] == "bestaudio/best"
    assert fake.options["postprocessors"] == [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "320",
        }
    ]
    assert "cookiefile" not in fake.options
    assert "cookiesfrombrowser" not in fake.options


@pytest.mark.asyncio
async def test_provisional_import_recovers_request_when_quality_task_failed_first(
    tmp_path: Path,
):
    record = SimpleNamespace(
        musicbrainz_id="release-group",
        download_task_id="quality-task",
    )
    request_history = SimpleNamespace(
        async_get_record=AsyncMock(return_value=record),
        async_update_status=AsyncMock(),
    )
    store = SimpleNamespace(
        get_task=AsyncMock(
            return_value=SimpleNamespace(
                origin="youtube_upgrade",
                user_id="user-1",
            )
        )
    )
    on_import = AsyncMock()
    service = YouTubeProvisionalService(
        repository=MagicMock(),
        store=store,
        album_service=MagicMock(),
        file_processor=MagicMock(),
        preferences=MagicMock(),
        event_bus=MagicMock(),
        staging_path=tmp_path,
        naming_template="{title}",
        request_history=request_history,
        on_import_callback=on_import,
    )

    await service._sync_request_after_import(
        SimpleNamespace(
            id="youtube-task",
            user_id="user-1",
            release_group_mbid="release-group",
        ),
        DownloadStatus.COMPLETED,
    )

    request_history.async_update_status.assert_awaited_once()
    assert request_history.async_update_status.await_args.args[:2] == (
        "release-group",
        "imported",
    )
    on_import.assert_awaited_once_with(record)
