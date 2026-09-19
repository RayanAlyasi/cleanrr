"""Classify a Radarr/Sonarr queue record and render a reason a friend reads.

Verified against both services' `components.schemas.QueueResource`
(Radarr/Sonarr `openapi.json`): `status` is `QueueStatus`
(`unknown|queued|paused|downloading|completed|failed|warning|delay|
downloadClientUnavailable|fallback`), `trackedDownloadStatus` is
`ok|warning|error`, `trackedDownloadState` is
`downloading|importBlocked|importPending|importing|imported|failedPending|
failed|ignored`, `statusMessages` is `[{title, messages[]}]`, and
`errorMessage` is a string. Radarr and Sonarr expose exactly the same
fields for this purpose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from cleanrr.tools._qbittorrent_auth import normalize_torrent_hash
from cleanrr.tools._untrusted import bound_message_list, render_upstream_block

_MAX_RECORDS = 50
_MAX_MESSAGES = 3
_MAX_MESSAGE_CHARS = 120

QueueCode = Literal[
    "import_blocked",
    "failed",
    "client_unavailable",
    "ignored",
    "paused",
    "warning",
    "delayed",
    "import_pending",
    "ok",
]

# Worst first: the code a queue reports is the worst code any of its
# records has.
_CODE_ORDER: tuple[QueueCode, ...] = (
    "import_blocked",
    "failed",
    "client_unavailable",
    "ignored",
    "paused",
    "warning",
    "delayed",
    "import_pending",
    "ok",
)

_STUCK_CODES: frozenset[QueueCode] = frozenset(
    {"import_blocked", "failed", "client_unavailable", "ignored", "paused", "warning"}
)

_SENTENCES: dict[str, str] = {
    "import_blocked": "{service} downloaded it but could not import it.",
    "failed": "The download failed in {service}'s queue.",
    "client_unavailable": "{service} cannot reach its download client.",
    "ignored": "{service} is ignoring that download.",
    "paused": "The download is paused in the download client.",
    "warning": "{service} flagged a problem with this download.",
    "delayed": "{service} is holding off before grabbing a release.",
    "import_pending": "It finished downloading and is waiting to be imported.",
}


@dataclass(frozen=True)
class QueueDiagnosis:
    records: int
    # Records whose code equals `code`, the worst code found.
    flagged: int
    code: QueueCode
    messages: list[str]
    download_id: str | None


def _lower(value: object) -> str | None:
    # Casefolding is what makes a PascalCase fork behave.
    return value.casefold() if isinstance(value, str) else None


def _record_code(record: dict[str, Any]) -> QueueCode:
    state = _lower(record.get("trackedDownloadState"))
    tracked_status = _lower(record.get("trackedDownloadStatus"))
    status = _lower(record.get("status"))

    if state == "importblocked":
        return "import_blocked"
    if state in ("failed", "failedpending") or status == "failed":
        return "failed"
    if status == "downloadclientunavailable":
        return "client_unavailable"
    if state == "ignored":
        return "ignored"
    if status == "paused":
        return "paused"
    # `warning` outranks `import_pending` because Radarr's Warn() fires
    # after the state is already `importPending`.
    if tracked_status in ("warning", "error") or status == "warning":
        return "warning"
    if status == "delay":
        return "delayed"
    if state == "importpending":
        return "import_pending"
    return "ok"


def _record_messages(record: dict[str, Any]) -> list[str]:
    # errorMessage is the download client's own message and the most
    # actionable line, so it goes first — statusMessages[].title is the
    # download item's own name, not a reason, so it is left out.
    raw: list[object] = [record.get("errorMessage")]
    status_messages = record.get("statusMessages")
    if isinstance(status_messages, list):
        for item in status_messages[:_MAX_MESSAGES]:
            if not isinstance(item, dict):
                continue
            messages = item.get("messages")
            if isinstance(messages, list):
                raw.extend(messages[:_MAX_MESSAGES])
    return bound_message_list(raw, limit=_MAX_MESSAGE_CHARS, max_items=_MAX_MESSAGES)


def diagnose_queue(records: object) -> QueueDiagnosis:
    if not isinstance(records, list):
        return QueueDiagnosis(records=0, flagged=0, code="ok", messages=[], download_id=None)

    count = 0
    codes: list[QueueCode] = []
    worst_record: dict[str, Any] | None = None
    worst_code: QueueCode = "ok"
    worst_rank = _CODE_ORDER.index("ok")

    for record in records[:_MAX_RECORDS]:
        if not isinstance(record, dict):
            continue
        count += 1
        code = _record_code(record)
        codes.append(code)
        rank = _CODE_ORDER.index(code)
        if rank < worst_rank:
            worst_rank = rank
            worst_record = record
            worst_code = code

    if worst_record is None:
        return QueueDiagnosis(records=count, flagged=0, code="ok", messages=[], download_id=None)

    # Radarr/Sonarr set downloadId to the download client's id and
    # qBittorrent's is the infohash, so a non-torrent client's id simply
    # fails the check.
    return QueueDiagnosis(
        records=count,
        flagged=codes.count(worst_code),
        code=worst_code,
        messages=_record_messages(worst_record),
        download_id=normalize_torrent_hash(worst_record.get("downloadId")),
    )


def render_queue_reason(
    diagnosis: QueueDiagnosis, *, service: str, include_download_id: bool
) -> str:
    sentence = _SENTENCES.get(diagnosis.code)
    if sentence is None:
        return ""

    parts = [sentence.format(service=service)]
    if diagnosis.flagged > 1:
        parts.append(f"{diagnosis.flagged} of {diagnosis.records} queued items are affected.")
    if include_download_id and diagnosis.download_id is not None and diagnosis.code in _STUCK_CODES:
        parts.append(f"Torrent hash: {diagnosis.download_id}.")

    result = " ".join(parts)
    block = render_upstream_block(service, diagnosis.messages)
    if block:
        result += "\n" + block
    return result
