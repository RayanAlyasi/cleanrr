from __future__ import annotations

from typing import Any

import pytest

from cleanrr.tools._queue_reason import QueueDiagnosis, diagnose_queue, render_queue_reason

# ---------------------------------------------------------------------------
# diagnose_queue — shape guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("records", [None, "x", {}, []])
def test_diagnose_queue_non_list_or_empty(records: object) -> None:
    diagnosis = diagnose_queue(records)
    assert diagnosis.records == 0
    assert diagnosis.code == "ok"
    assert diagnosis.messages == []
    assert diagnosis.download_id is None


def test_diagnose_queue_counts_empty_dicts_as_ok() -> None:
    diagnosis = diagnose_queue([{}, {}])
    assert diagnosis.records == 2
    assert diagnosis.flagged == 0
    assert diagnosis.code == "ok"


def test_diagnose_queue_skips_non_dict_entries_without_counting() -> None:
    diagnosis = diagnose_queue([{}, "x", 3])
    assert diagnosis.records == 1


def test_diagnose_queue_caps_scan_at_50_records() -> None:
    records: list[dict[str, Any]] = [{} for _ in range(60)]
    records[54] = {"trackedDownloadState": "importBlocked"}
    diagnosis = diagnose_queue(records)
    assert diagnosis.records == 50
    assert diagnosis.code == "ok"


# ---------------------------------------------------------------------------
# diagnose_queue — one code per row of the Design table, plus PascalCase
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("record", "expected_code"),
    [
        ({"trackedDownloadState": "importBlocked"}, "import_blocked"),
        ({"trackedDownloadState": "ImportBlocked"}, "import_blocked"),
        ({"trackedDownloadState": "failed"}, "failed"),
        ({"trackedDownloadState": "failedPending"}, "failed"),
        ({"status": "failed"}, "failed"),
        ({"status": "downloadClientUnavailable"}, "client_unavailable"),
        ({"trackedDownloadState": "ignored"}, "ignored"),
        ({"status": "paused"}, "paused"),
        ({"trackedDownloadStatus": "warning"}, "warning"),
        ({"trackedDownloadStatus": "Warning"}, "warning"),
        ({"trackedDownloadStatus": "error"}, "warning"),
        ({"status": "warning"}, "warning"),
        ({"status": "delay"}, "delayed"),
        ({"trackedDownloadState": "importPending"}, "import_pending"),
        ({}, "ok"),
        ({"status": "unknown"}, "ok"),
    ],
)
def test_diagnose_queue_classifies_each_code(record: dict[str, Any], expected_code: str) -> None:
    assert diagnose_queue([record]).code == expected_code


# ---------------------------------------------------------------------------
# diagnose_queue — precedence
# ---------------------------------------------------------------------------


def test_diagnose_queue_import_blocked_outranks_paused() -> None:
    record = {"trackedDownloadState": "importBlocked", "status": "paused"}
    assert diagnose_queue([record]).code == "import_blocked"


def test_diagnose_queue_status_failed_outranks_warning() -> None:
    record = {"status": "failed", "trackedDownloadStatus": "warning"}
    assert diagnose_queue([record]).code == "failed"


def test_diagnose_queue_warning_outranks_import_pending() -> None:
    # The traced upstream case: CompletedDownloadService sets
    # State = ImportPending, then Warn() sets Status = Warning.
    record = {
        "trackedDownloadState": "importPending",
        "trackedDownloadStatus": "warning",
        "statusMessages": [
            {
                "title": "Some.Release.Name-GRP",
                "messages": ["No files found are eligible for import in /downloads/x"],
            }
        ],
    }
    diagnosis = diagnose_queue([record])
    assert diagnosis.code == "warning"
    assert "Some.Release.Name-GRP" not in diagnosis.messages
    assert "No files found are eligible for import in /downloads/x" in diagnosis.messages


def test_diagnose_queue_worst_of_many() -> None:
    records = [
        {},
        {"trackedDownloadState": "importPending"},
        {"trackedDownloadState": "importBlocked"},
    ]
    diagnosis = diagnose_queue(records)
    assert diagnosis.code == "import_blocked"
    assert diagnosis.flagged == 2
    assert diagnosis.records == 3


# ---------------------------------------------------------------------------
# diagnose_queue — messages
# ---------------------------------------------------------------------------


def test_diagnose_queue_collects_error_message_alone() -> None:
    record = {"trackedDownloadState": "importBlocked", "errorMessage": "disk full"}
    assert diagnose_queue([record]).messages == ["disk full"]


def test_diagnose_queue_drops_duplicate_messages() -> None:
    record = {
        "trackedDownloadState": "importBlocked",
        "statusMessages": [{"messages": ["same"]}],
        "errorMessage": "same",
    }
    assert diagnose_queue([record]).messages == ["same"]


def test_diagnose_queue_cuts_each_message_to_120_chars() -> None:
    record = {"trackedDownloadState": "importBlocked", "errorMessage": "x" * 200}
    messages = diagnose_queue([record]).messages
    assert messages == ["x" * 120]


def test_diagnose_queue_malformed_status_messages_yields_no_messages() -> None:
    record = {
        "trackedDownloadState": "importBlocked",
        "statusMessages": [
            {"title": "x", "messages": None},
            {"messages": "not a list"},
            "junk",
        ],
    }
    diagnosis = diagnose_queue([record])
    assert diagnosis.messages == []
    assert diagnosis.code == "import_blocked"


# ---------------------------------------------------------------------------
# diagnose_queue — download_id
# ---------------------------------------------------------------------------


def test_diagnose_queue_download_id_lowercased_hash() -> None:
    record = {"trackedDownloadState": "importBlocked", "downloadId": "A" * 40}
    assert diagnose_queue([record]).download_id == "a" * 40


def test_diagnose_queue_download_id_none_for_non_hash() -> None:
    record = {"trackedDownloadState": "importBlocked", "downloadId": "SABnzbd_nzo_abc"}
    assert diagnose_queue([record]).download_id is None


def test_diagnose_queue_download_id_none_when_code_is_ok() -> None:
    record = {"downloadId": "a" * 40}
    assert diagnose_queue([record]).download_id is None


# ---------------------------------------------------------------------------
# render_queue_reason
# ---------------------------------------------------------------------------


def test_render_queue_reason_empty_for_ok() -> None:
    diagnosis = diagnose_queue([{}])
    assert render_queue_reason(diagnosis, service="Radarr", include_download_id=False) == ""


def test_render_queue_reason_contains_service() -> None:
    diagnosis = diagnose_queue([{"trackedDownloadState": "importBlocked"}])
    result = render_queue_reason(diagnosis, service="Sonarr", include_download_id=False)
    assert "Sonarr" in result


def test_render_queue_reason_affected_count_only_when_more_than_one() -> None:
    single = diagnose_queue([{"trackedDownloadState": "importBlocked"}])
    multiple = diagnose_queue(
        [
            {"trackedDownloadState": "importPending"},
            {"trackedDownloadState": "importBlocked"},
        ]
    )
    single_result = render_queue_reason(single, service="Radarr", include_download_id=False)
    multiple_result = render_queue_reason(multiple, service="Radarr", include_download_id=False)
    assert "queued items are affected" not in single_result
    assert "2 of 2 queued items are affected." in multiple_result


def test_render_queue_reason_hash_only_when_requested_and_stuck() -> None:
    stuck_hash = "b" * 40
    stuck = diagnose_queue([{"trackedDownloadState": "importBlocked", "downloadId": stuck_hash}])
    assert stuck_hash in render_queue_reason(stuck, service="Radarr", include_download_id=True)
    assert stuck_hash not in render_queue_reason(stuck, service="Radarr", include_download_id=False)

    delayed_hash = "c" * 40
    delayed = diagnose_queue([{"status": "delay", "downloadId": delayed_hash}])
    assert delayed.download_id == delayed_hash
    assert delayed_hash not in render_queue_reason(
        delayed, service="Radarr", include_download_id=True
    )


def test_render_queue_reason_upstream_newlines_cannot_add_lines() -> None:
    record = {
        "trackedDownloadState": "importBlocked",
        "statusMessages": [{"messages": ["line1\nline2\tcontrol\x07here"]}],
        "errorMessage": "err\nmsg",
    }
    diagnosis = diagnose_queue([record])
    result = render_queue_reason(diagnosis, service="Radarr", include_download_id=False)
    assert len(result.splitlines()) == 2 + len(diagnosis.messages)


def test_render_queue_reason_requires_explicit_include_download_id() -> None:
    diagnosis = QueueDiagnosis(
        records=1, flagged=1, code="import_blocked", messages=[], download_id="a" * 40
    )
    with pytest.raises(TypeError):
        render_queue_reason(diagnosis, service="Radarr")  # type: ignore[call-arg]
