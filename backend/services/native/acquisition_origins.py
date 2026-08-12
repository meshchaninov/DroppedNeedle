"""Shared acquisition-origin semantics."""

# ``youtube_upgrade`` starts as a normal quota-bearing user request before its
# temporary copy exists, but may safely replace that copy once it lands.
REPLACEMENT_ORIGINS = frozenset({"upgrade", "youtube_upgrade"})


def allows_replacement(origin: str) -> bool:
    return origin in REPLACEMENT_ORIGINS
