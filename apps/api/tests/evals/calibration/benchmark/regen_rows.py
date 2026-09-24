"""Regenerate the benchmark's real answers under the platform prompt and score them with the
grounding rule. Spends money: one agent-turn model call per answer.

    python regen_rows.py --spend [--without-rules] [--seed 7] [--limit N] [--out FILE]

Retrieval is fixed: each answer is generated from its row's `question` and the same
`retrieved_contexts` the stored answer saw, handed to the model as one retrieve tool result.
The soul is the platform default, so the number measures the template and nothing a
tenant wrote. `--without-rules` strips the four grounding rules from the MUST block, which
is how the before figure in `.dev/reference/260924-grounding-prompt-rules.md` is produced.

What this is not: a live turn. It sends no conversation history, so a follow-up question is
answered cold; it offers no tools, so clarify and escalate cannot happen; it hands the
retrieved text over as one chunk where the live retrieve tool frames and caps several; and
a tenant's soul adds its own rules. The stored answers in `rows.csv` came from the staging
agent's live loop and pass 8 of 30. Read the figures here against their own baseline.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import pathlib
import statistics
import sys
from types import SimpleNamespace

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))

from app.core.model_client import resolve_credentials, route_for  # noqa: E402
from app.domain.grounding import ground  # noqa: E402
from app.services import agent_prompt  # noqa: E402

GROUNDING_RULES_START = "- Build each factual sentence from the words of the passage"
GROUNDING_RULES_END = "end with one CITATIONS block.\n"
AGENT_TURN = "agent_turn"


def real_rows() -> list[dict]:
    with (HERE / "rows.csv").open(newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if not r["scenario_id"].endswith("-seeded")]


def template(without_rules: bool) -> str:
    text = agent_prompt._TEMPLATE
    if without_rules:
        start = text.index(GROUNDING_RULES_START)
        end = text.index(GROUNDING_RULES_END, start) + len(GROUNDING_RULES_END)
        text = text[:start] + text[end:]
    return text


def system_prompt(without_rules: bool) -> str:
    agent = SimpleNamespace(
        name="the owner", soul_role=None, soul_voice=None,
        soul_do_list=[], soul_donot_list=[], soul=None,
    )
    role, voice, _do, _donot = agent_prompt._resolved_soul(agent, {})
    return template(without_rules).format(
        role=role, name=agent.name, voice=voice,
        do_block="- Answer questions accurately based on retrieved content",
        donot_block="- Make up information not present in retrieved content",
        few_shot=agent_prompt.FEW_SHOT_SUFFIX,
    )


def messages_for(row: dict, prompt: str) -> list[dict]:
    call_id = "call_retrieve_1"
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": row["question"]},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": "retrieve", "arguments": json.dumps({"query": row["question"]})},
        }]},
        {"role": "tool", "tool_call_id": call_id,
         "content": json.dumps({"chunks": [{"content": row["retrieved_contexts"]}]})},
    ]


async def generate(rows: list[dict], prompt: str, seed: int) -> list[dict]:
    from openai import AsyncOpenAI

    route = route_for(AGENT_TURN)
    credentials = resolve_credentials(route.provider)
    client = AsyncOpenAI(api_key=credentials.api_key, base_url=credentials.base_url, timeout=120)
    gate = asyncio.Semaphore(4)
    out: list[dict] = []

    async def one(row: dict) -> None:
        kwargs: dict = {"model": route.model, "messages": messages_for(row, prompt), "seed": seed}
        if route.reasoning_effort is not None:
            kwargs["reasoning_effort"] = route.reasoning_effort
        async with gate:
            try:
                completion = await client.chat.completions.create(**kwargs)
                text = completion.choices[0].message.content or ""
            except Exception as exc:
                # One row's failure is reported and kept as an empty answer, which
                # `score` counts under `empty` rather than as a measurement.
                text = ""
                print("ERROR", row["scenario_id"][:8], type(exc).__name__, exc, file=sys.stderr)
        out.append({**row, "response": text})

    await asyncio.gather(*(one(r) for r in rows))
    out.sort(key=lambda r: r["scenario_id"])
    return out


def score(regenerated: list[dict], threshold: float = 0.80) -> dict:
    scores: list[float] = []
    flagged = total = empty = 0
    for r in regenerated:
        if not r["response"]:
            empty += 1
            continue
        g = ground(r["response"], [r["retrieved_contexts"]])
        if g.score is not None:
            scores.append(g.score)
        flagged += sum(1 for s in g.sentences if not s.supported)
        total += len(g.sentences)
    scores.sort()
    return {
        "answers": len(regenerated), "empty": empty, "scored": len(scores),
        "pass_at_threshold": sum(1 for s in scores if s >= threshold),
        "median": round(statistics.median(scores), 3) if scores else None,
        "min": round(scores[0], 3) if scores else None,
        "sentences": total, "sentences_flagged": flagged,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spend", action="store_true", help="confirm one model call per answer")
    parser.add_argument("--without-rules", action="store_true", help="strip the four grounding rules")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--limit", type=int, default=0, help="first N rows only")
    parser.add_argument("--out", type=pathlib.Path, default=None, help="CSV path; default regen-<label>-seed<seed>.csv beside rows.csv")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if not args.spend:
        print("This script makes one model call per answer. Pass --spend to run it.")
        return 2
    label = "without-rules" if args.without_rules else "with-rules"
    out = args.out or HERE / f"regen-{label}-seed{args.seed}.csv"
    rows = real_rows()
    selected = rows[: args.limit] if args.limit else rows
    regenerated = asyncio.run(generate(selected, system_prompt(args.without_rules), args.seed))
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(regenerated[0].keys()))
        writer.writeheader()
        writer.writerows(regenerated)
    print(label, f"seed={args.seed}", json.dumps(score(regenerated)))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
