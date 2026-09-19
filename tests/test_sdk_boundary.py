"""Asserts the argv the real SDK transport builds from cleanrr's own options.

`Agent.start()` hands a `ClaudeAgentOptions` to the SDK, and the SDK — not
cleanrr — translates it into the CLI flags that actually run. The
confirmation gate (`can_use_tool`) depends on that translation never
auto-approving a write tool via `--allowedTools`, and the sandboxing depends
on `--tools` staying empty and `--strict-mcp-config` staying set. Isolation
depends on the CLI being told to load no filesystem settings and to run in a
pinned empty directory. These tests build the real command line with
`SubprocessCLITransport._build_command()` and never call `connect()`,
`query()` or `receive_response()` — no subprocess, no network, no API key.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from claude_agent_sdk import ClaudeAgentOptions, CLINotFoundError
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from pydantic import HttpUrl, SecretStr

from cleanrr.agent import DEFAULT_SYSTEM_PROMPT, Agent, _isolated_cwd
from cleanrr.bot import (
    _build_overseerr_client,
    _build_qbit_client,
    _build_radarr_client,
    _build_sonarr_client,
)
from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.permissions import WRITE_TOOLS, ConfirmationRegistry

_FAKE_CLI_PATH = "/usr/bin/claude"

_captured: dict[str, ClaudeAgentOptions] = {}


class _CaptureSDKClient:
    def __init__(self, options: ClaudeAgentOptions) -> None:
        _captured["options"] = options

    async def __aenter__(self) -> _CaptureSDKClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


def _settings() -> Settings:
    return Settings(
        telegram_bot_token=SecretStr("test"),
        anthropic_api_key=SecretStr("sk-test"),
        overseerr_url=HttpUrl("http://overseerr:5055"),
        overseerr_api_key=SecretStr("ov-key"),
        sonarr_url=HttpUrl("http://sonarr:8989"),
        sonarr_api_key=SecretStr("so-key"),
        radarr_url=HttpUrl("http://radarr:7878"),
        radarr_api_key=SecretStr("ra-key"),
        qbittorrent_url=HttpUrl("http://qbit:8080"),
        qbittorrent_username="u",
        qbittorrent_password=SecretStr("p"),
        admin_telegram_ids={7},
    )


async def _capture_options(monkeypatch: pytest.MonkeyPatch) -> ClaudeAgentOptions:
    _captured.clear()
    from cleanrr import agent as agent_module

    settings = _settings()
    agent = Agent(
        identity=MagicMock(spec=Identity),
        settings=settings,
        timeout_seconds=5.0,
        telegram_bot=MagicMock(),
        overseerr_client=_build_overseerr_client(settings),
        sonarr_client=_build_sonarr_client(settings),
        radarr_client=_build_radarr_client(settings),
        qbit_client=_build_qbit_client(settings),
        confirmation_registry=ConfirmationRegistry(ttl_seconds=60),
    )
    monkeypatch.setattr(agent_module, "ClaudeSDKClient", _CaptureSDKClient)
    await agent.start(7)
    await agent.stop()
    return _captured["options"]


def _transport(options: ClaudeAgentOptions) -> SubprocessCLITransport:
    options.cli_path = _FAKE_CLI_PATH
    return SubprocessCLITransport(prompt="", options=options)


def _flag_value(cmd: list[str], flag: str) -> str:
    assert flag in cmd, f"{flag} is missing from the built CLI command"
    return cmd[cmd.index(flag) + 1]


async def _cli_command(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    options = await _capture_options(monkeypatch)
    transport = _transport(options)
    cmd = transport._build_command()
    assert transport._process is None
    return cmd


async def test_tools_flag_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = await _cli_command(monkeypatch)
    assert _flag_value(cmd, "--tools") == ""


async def test_no_write_tool_reaches_the_allowed_tools_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmd = await _cli_command(monkeypatch)
    allowed = _flag_value(cmd, "--allowedTools")
    for name in WRITE_TOOLS:
        assert name not in allowed, (
            f"{name} reached --allowedTools — an allowed_tools entry is "
            "auto-approved and skips can_use_tool entirely"
        )


async def test_read_tools_reach_the_allowed_tools_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmd = await _cli_command(monkeypatch)
    allowed = _flag_value(cmd, "--allowedTools")
    for name in ("list_my_requests", "find_my_request", "list_stalled_torrents"):
        assert name in allowed


async def test_strict_mcp_config_flag_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = await _cli_command(monkeypatch)
    assert "--strict-mcp-config" in cmd


async def test_setting_sources_flag_loads_no_filesystem_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmd = await _cli_command(monkeypatch)
    sources = [arg for arg in cmd if arg.startswith("--setting-sources")]
    assert sources == ["--setting-sources="], (
        "a permissions.allow entry in a filesystem settings file auto-approves "
        "that tool and skips can_use_tool entirely"
    )
    assert "--settings" not in cmd, (
        "--settings makes the CLI read settings from a path that setting_sources=[] does not gate"
    )
    assert "--plugin-dir" not in cmd, (
        "--plugin-dir makes the CLI read plugin content from a path that "
        "setting_sources=[] does not gate"
    )
    assert "--add-dir" not in cmd, (
        "--add-dir makes the CLI read settings from a path that setting_sources=[] does not gate"
    )


async def test_the_cli_runs_in_a_directory_with_no_claude_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = await _capture_options(monkeypatch)
    transport = _transport(options)
    assert transport._cwd is not None
    assert transport._cwd == str(_isolated_cwd())
    cwd = Path(transport._cwd)
    assert cwd.is_dir()
    assert cwd.name.startswith("cleanrr-agent-")
    assert not (cwd / ".claude").exists()
    assert transport._cwd != str(Path.cwd())


async def test_system_prompt_flag_carries_the_project_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmd = await _cli_command(monkeypatch)
    assert _flag_value(cmd, "--system-prompt") == DEFAULT_SYSTEM_PROMPT


async def test_mcp_config_registers_the_cleanrr_server_without_its_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmd = await _cli_command(monkeypatch)
    server = json.loads(_flag_value(cmd, "--mcp-config"))["mcpServers"]["cleanrr"]
    assert server["type"] == "sdk"
    assert "instance" not in server


async def test_no_permission_mode_flag_when_the_confirmation_callback_is_wired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmd = await _cli_command(monkeypatch)
    assert "--permission-mode" not in cmd


async def test_building_the_command_starts_no_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = await _capture_options(monkeypatch)
    transport = _transport(options)
    transport._build_command()
    assert transport._process is None
    assert transport._stdin_stream is None
    assert transport._stdout_stream is None


async def test_command_build_refuses_an_unresolved_cli_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = await _capture_options(monkeypatch)
    assert options.cli_path is None
    transport = SubprocessCLITransport(prompt="", options=options)
    with pytest.raises(CLINotFoundError):
        transport._build_command()
