from unittest.mock import patch

import cleanrr.metrics as metrics


def _exported_name(metric: object) -> str:
    return next(iter(metric.collect())).name  # type: ignore[attr-defined]


def _label_names(metric: object) -> list[str]:
    return list(metric._labelnames)  # type: ignore[attr-defined]


def test_metrics_have_expected_names_and_labels() -> None:
    assert _exported_name(metrics.up) == "cleanrr_up"
    assert _exported_name(metrics.telegram_messages_total) == "cleanrr_telegram_messages"
    assert _label_names(metrics.telegram_messages_total) == ["kind", "command"]
    assert _exported_name(metrics.claude_requests_total) == "cleanrr_claude_requests"
    assert _label_names(metrics.claude_requests_total) == ["status"]
    assert (
        _exported_name(metrics.claude_request_duration_seconds)
        == "cleanrr_claude_request_duration_seconds"
    )
    assert _exported_name(metrics.link_codes_issued_total) == "cleanrr_link_codes_issued"
    assert _exported_name(metrics.link_codes_redeemed_total) == "cleanrr_link_codes_redeemed"
    assert _label_names(metrics.link_codes_redeemed_total) == ["status"]
    assert _exported_name(metrics.linked_users) == "cleanrr_linked_users"


def test_start_calls_prometheus_http_server() -> None:
    with patch("cleanrr.metrics.start_http_server") as mock_server:
        metrics.start(9100)
        mock_server.assert_called_once_with(9100)
        assert metrics.up._value.get() == 1.0  # type: ignore[attr-defined]
