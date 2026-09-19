"""Report what a pipeline run cost, per agent and for the orchestrating session.

    python .claude/hooks/usage_report.py --session <session-id> [--since 2026-09-18T16:38]

Reads Claude Code's own transcripts under ~/.claude/projects/. Token counts are exact;
the dollar column is API list price, used as a common unit (a subscription draws down
its usage window instead). Cost is dominated by context size times turns, so the turn
count and the context-at-end column are the numbers to watch.
"""

import argparse
import json
import sys
from pathlib import Path

# USD per million tokens: (base input, output). Source: platform.claude.com pricing, 2026-09-19.
# Cache read is 0.1x base input (0.025x on Fable). A cache write is 1.25x at the 5-minute TTL
# and 2x at the 1-hour TTL; subagents write at 5 minutes, the main session at 1 hour.
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
TOKEN_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)
SHORT_WRITE_KEY = "ephemeral_5m_input_tokens"


def _price(model: str) -> tuple[float, float, float] | None:
    for prefix, (base, out) in PRICES.items():
        if model.startswith(prefix):
            return base, out, 0.025 if "fable" in prefix else 0.1
    return None


def summarise(
    transcript: Path, since: str = "", sidechain: bool | None = None
) -> dict[str, object]:
    per_message: dict[str, dict[str, int]] = {}
    model = ""
    for line in transcript.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message")
        if entry.get("type") != "assistant" or not isinstance(message, dict):
            continue
        if sidechain is not None and bool(entry.get("isSidechain")) != sidechain:
            continue
        if since and str(entry.get("timestamp", "")) < since:
            continue
        message_id = message.get("id")
        usage = message.get("usage") or {}
        if not message_id or str(message.get("model", "")).startswith("<"):
            continue
        model = message.get("model") or model
        # A streamed message appears once per content block; keep the largest count seen.
        seen = per_message.setdefault(message_id, dict.fromkeys((*TOKEN_KEYS, SHORT_WRITE_KEY), 0))
        for key in TOKEN_KEYS:
            seen[key] = max(seen[key], int(usage.get(key) or 0))
        breakdown = usage.get("cache_creation") or {}
        seen[SHORT_WRITE_KEY] = max(seen[SHORT_WRITE_KEY], int(breakdown.get(SHORT_WRITE_KEY) or 0))
    totals = {key: sum(m[key] for m in per_message.values()) for key in TOKEN_KEYS}
    short_writes = sum(m[SHORT_WRITE_KEY] for m in per_message.values())
    last = list(per_message.values())[-1] if per_message else dict.fromkeys(TOKEN_KEYS, 0)
    context_end = (
        last["input_tokens"] + last["cache_creation_input_tokens"] + last["cache_read_input_tokens"]
    )
    cost = None
    price = _price(model)
    if price is not None:
        base, out, read_mult = price
        cost = (
            totals["input_tokens"] * base
            + short_writes * base * 1.25
            # A write with no TTL breakdown is priced at the 1-hour rate.
            + (totals["cache_creation_input_tokens"] - short_writes) * base * 2
            + totals["cache_read_input_tokens"] * base * read_mult
            + totals["output_tokens"] * out
        ) / 1e6
    return {
        "model": model,
        "turns": len(per_message),
        "context_end": context_end,
        "cost": cost,
        **totals,
    }


def _find_session(session: str) -> Path | None:
    matches = list((Path.home() / ".claude" / "projects").glob(f"*/{session}.jsonl"))
    return matches[0] if matches else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument(
        "--since", default="", help="ISO timestamp prefix; only count turns at or after it"
    )
    args = parser.parse_args()

    main_transcript = _find_session(args.session)
    if main_transcript is None:
        print(f"no transcript found for session {args.session}")
        return 1
    rows: list[tuple[str, dict[str, object]]] = [
        ("orchestrator", summarise(main_transcript, args.since, sidechain=False))
    ]
    subagents = main_transcript.with_suffix("") / "subagents"
    for transcript in sorted(subagents.glob("agent-*.jsonl"), key=lambda p: p.stat().st_mtime):
        label = transcript.stem
        meta = transcript.with_suffix(".meta.json")
        if meta.exists():
            info = json.loads(meta.read_text(encoding="utf-8"))
            label = f"{info.get('agentType', '?')}: {info.get('description', '')}"[:44]
        summary = summarise(transcript, args.since)
        if summary["turns"]:
            rows.append((label, summary))

    row = "{:44s} {:16s} {:>5} {:>11} {:>10} {:>8} {:>8} {:>7}"
    print(
        row.format("agent", "model", "turns", "cache rd", "cache wr", "output", "ctx end", "$ list")
    )
    total = 0.0
    for label, s in rows:
        cost = s["cost"]
        total += cost if isinstance(cost, float) else 0.0
        print(
            row.format(
                label,
                str(s["model"])[:16],
                s["turns"],
                f"{s['cache_read_input_tokens']:,}",
                f"{s['cache_creation_input_tokens']:,}",
                f"{s['output_tokens']:,}",
                f"{s['context_end']:,}",
                f"{cost:.2f}" if isinstance(cost, float) else "?",
            )
        )
    turns = sum(int(str(s["turns"])) for _, s in rows)
    print(row.format("TOTAL", "", turns, "", "", "", "", f"{total:.2f}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
