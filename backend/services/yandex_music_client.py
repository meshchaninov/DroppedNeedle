"""DroppedNeedle adapter around the maintained ``yandex-music`` library."""

from __future__ import annotations

from yandex_music import ClientAsync
from yandex_music.exceptions import UnauthorizedError, YandexMusicError


class YandexMusicAuthError(Exception):
    """The supplied Yandex Music token is missing or no longer authorized."""


class YandexMusicClient:
    """Keep library models and transport details out of the application services."""

    def __init__(self, token: str, *, user_id: str = "", username: str = "") -> None:
        self._client = ClientAsync(token, language="en")
        self.user_id = user_id
        self.username = username

    async def get_account(self) -> dict[str, str]:
        try:
            status = await self._client.account_status()
        except UnauthorizedError as exc:
            raise YandexMusicAuthError("Yandex Music token is invalid or expired") from exc
        except YandexMusicError:
            raise
        account = status.account if status else None
        if account is None or account.uid is None:
            raise YandexMusicAuthError("Yandex Music account is unavailable")
        self.user_id = str(account.uid)
        self._client.account_uid = account.uid
        return {
            "uid": self.user_id,
            "username": account.display_name or account.login or self.user_id,
        }

    async def get_liked_tracks(self, stop_at_ids: set[str] | None = None) -> list[dict]:
        if not self.user_id:
            await self.get_account()
        try:
            liked = await self._client.users_likes_tracks(self.user_id)
        except UnauthorizedError as exc:
            raise YandexMusicAuthError("Yandex Music token is invalid or expired") from exc
        if liked is None:
            return []

        short_tracks = sorted(liked.tracks, key=lambda item: item.timestamp or "", reverse=True)
        selected = []
        for item in short_tracks:
            track_id = str(item.id)
            if stop_at_ids and track_id in stop_at_ids:
                break
            selected.append(item)

        detailed = {}
        for offset in range(0, len(selected), 50):
            batch = selected[offset : offset + 50]
            try:
                tracks = await self._client.tracks([item.track_id for item in batch])
            except UnauthorizedError as exc:
                raise YandexMusicAuthError("Yandex Music token is invalid or expired") from exc
            detailed.update({str(track.id): track for track in tracks})

        result: list[dict] = []
        for item in selected:
            track = detailed.get(str(item.id))
            if track is None:
                continue
            album = track.albums[0] if track.albums else None
            result.append(
                {
                    "added_at": item.timestamp or "",
                    "track": {
                        "id": str(track.id),
                        "title": track.title,
                        "artists": [
                            {"name": artist.name} for artist in track.artists if artist.name
                        ],
                        "albums": [{"title": album.title}] if album and album.title else [],
                        "duration_ms": track.duration_ms,
                    },
                }
            )
        return result
