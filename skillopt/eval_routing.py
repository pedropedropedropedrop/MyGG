#!/usr/bin/env python3
"""Dry-run tool-ROUTING eval for GG.

Feeds each request through GG's REAL router (agent._call_model_with_retry with
agent._build_system_prompt + agent._get_all_tool_definitions) and records which
tool the model chose as its FIRST tool_use block. It never runs the agent loop
and never dispatches a tool, so it has ZERO side effects — no email is sent, no
event created. As a hard guarantee, agent._execute_tool is monkeypatched to raise.

This is (a) a standalone routing regression test and (b) the measurement function
a SkillOpt-style strict-improvement gate would optimize against.

Usage (from anywhere, but use GG's venv python):
  venv\\Scripts\\python.exe skillopt\\eval_routing.py                # full set
  venv\\Scripts\\python.exe skillopt\\eval_routing.py --split val    # one split
  venv\\Scripts\\python.exe skillopt\\eval_routing.py --limit 3      # smoke
  venv\\Scripts\\python.exe skillopt\\eval_routing.py --selftest     # offline scorer check, no API
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
EVAL_FILE = HERE / "gg_routing_eval.jsonl"

agent = None  # set in _import_gg after env is loaded


def load_tasks(split=None, limit=None):
    tasks = []
    for line in EVAL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        t = json.loads(line)
        if split and t.get("split") != split:
            continue
        tasks.append(t)
    if limit:
        tasks = tasks[:limit]
    return tasks


def score(task, got_tool, got_args):
    """(hard, soft) for one routing decision. Pure function — see selftest().

    hard = 1.0 iff chosen tool is the expected tool (or in its `acceptable` set).
    soft = 0.6*name_match + 0.4*arg_subset_ratio, arg credit gated on name match.
    ponytail: name is the primary signal; args are partial credit so tiny val
    sets still move the strict-improvement gate. Upgrade to per-arg weighting only
    if arg precision starts mattering.
    """
    expected = task["expected_tool"]
    acceptable = set([expected] + task.get("acceptable", []))
    name_ok = got_tool in acceptable
    exp_args = task.get("expected_args") or {}
    if not name_ok:
        return 0.0, 0.0
    if not exp_args:
        arg_ratio = 1.0
    else:
        got_args = got_args or {}
        matched = sum(
            1 for k, v in exp_args.items()
            if str(got_args.get(k)).lower() == str(v).lower()
        )
        arg_ratio = matched / len(exp_args)
    return 1.0, 0.6 + 0.4 * arg_ratio


# Anthropic runs these server-side to orchestrate web_search/web_fetch (dynamic
# filtering). They show up FIRST in content but are NOT a routing choice — skip
# them and report the real intent tool, falling back to them only if nothing else.
_SERVER_WRAPPER = {"code_execution", "bash_code_execution"}


def first_tool(response):
    """The tool GG actually routed to. Prefers the first local tool_use or
    web_search/web_fetch; treats code_execution/bash_code_execution as a
    server-side wrapper (only reported if it's the sole tool, e.g. a pure calc)."""
    fallback = None
    for b in response.content:
        if getattr(b, "type", None) in ("tool_use", "server_tool_use"):
            args = dict(getattr(b, "input", {}) or {})
            if b.name in _SERVER_WRAPPER:
                if fallback is None:
                    fallback = (b.name, args)
                continue
            return b.name, args
    return fallback if fallback else (None, {})


def _import_gg():
    """Load env, import GG, discover modules, and hard-disable tool dispatch."""
    global agent
    from dotenv import load_dotenv
    load_dotenv(PROJECT / ".env")
    os.chdir(PROJECT)
    sys.path.insert(0, str(PROJECT))
    import modules
    import agent as _agent
    import tools, tempfile
    global _scratch
    _scratch = tempfile.TemporaryDirectory(prefix="mygg-routing-")
    tools.DB_PATH = Path(_scratch.name) / "routing.db"
    tools.init_db()
    modules.discover()

    async def _forbidden(*a, **k):
        raise AssertionError("routing eval must never dispatch a tool")
    _agent._execute_tool = _forbidden  # belt-and-suspenders: no side effects, ever
    agent = _agent


async def run_task(task, sem, system, tool_defs):
    async with sem:
        messages = [{"role": "user", "content": task["request"]}]
        resp, err = await agent._call_model_with_retry(agent.MODEL, system, tool_defs, messages)
        if err or resp is None:
            return {**task, "got_tool": None, "got_args": {}, "hard": 0.0, "soft": 0.0, "error": err or "no response"}
        got_tool, got_args = first_tool(resp)
        hard, soft = score(task, got_tool, got_args)
        return {**task, "got_tool": got_tool, "got_args": got_args, "hard": hard, "soft": soft, "error": None}


def aggregate(raw):
    """Group per-trial results by task id into per-task pass-rates + got-tool tally."""
    from collections import Counter, defaultdict
    by = defaultdict(list)
    for r in raw:
        by[r["id"]].append(r)
    out = []
    for tid, rs in by.items():
        base = rs[0]
        out.append({
            "id": tid,
            "request": base["request"],
            "expected_tool": base["expected_tool"],
            "acceptable": base.get("acceptable", []),
            "trials": len(rs),
            "hard_rate": sum(x["hard"] for x in rs) / len(rs),
            "soft_rate": sum(x["soft"] for x in rs) / len(rs),
            "got": dict(Counter(x["got_tool"] for x in rs)),
        })
    return out


def report(agg, k):
    n = len(agg)
    hard = sum(r["hard_rate"] for r in agg) / n
    soft = sum(r["soft_rate"] for r in agg) / n
    print(f"\n=== GG tool-routing baseline ({n} tasks x{k} samples, model={agent.MODEL}) ===")
    print(f"hard (exact tool)  : {hard:.1%}   (mean over tasks of per-task pass-rate)")
    print(f"soft (tool+args)   : {soft:.1%}")

    imperfect = [r for r in agg if r["hard_rate"] < 1.0]
    stable_wrong = [r for r in imperfect if r["hard_rate"] == 0.0]
    flappy = [r for r in imperfect if 0.0 < r["hard_rate"] < 1.0]
    print(f"\nclean {n - len(imperfect)}/{n}   |   stable-wrong (0/{k}): {len(stable_wrong)}   |   flappy (noisy): {len(flappy)}")

    if stable_wrong:
        print("\nSTABLE-WRONG — real routing errors (never right across all samples):")
        for r in sorted(stable_wrong, key=lambda x: x["id"]):
            print(f"  [{r['id']}] expected {r['expected_tool']}  got {r['got']}")
            print(f"      \"{r['request'][:90]}\"")
    if flappy:
        print("\nFLAPPY — within sampling noise (sometimes right):")
        for r in sorted(flappy, key=lambda x: -x["hard_rate"]):
            print(f"  [{r['id']}] {r['hard_rate']:.0%} right  expected {r['expected_tool']}  got {r['got']}")

    out = HERE / "last_run.json"
    out.write_text(json.dumps(agg, indent=2, default=str), encoding="utf-8")
    print(f"\nfull results -> {out}")


def selftest():
    """Offline check of the scoring logic. No API, no imports of GG."""
    t_noargs = {"expected_tool": "recall", "expected_args": {}}
    t_args = {"expected_tool": "get_calendar", "expected_args": {"days": 7}}
    t_multi = {"expected_tool": "list_todos", "expected_args": {"status": "pending", "priority": "high"}}
    t_acc = {"expected_tool": "recall", "expected_args": {}, "acceptable": ["list_todos"]}

    assert score(t_noargs, "recall", {}) == (1.0, 1.0)
    assert score(t_noargs, "remember", {}) == (0.0, 0.0)
    assert score(t_args, "get_calendar", {"days": 7}) == (1.0, 1.0)
    assert score(t_args, "get_calendar", {"days": 1}) == (1.0, 0.6)          # name ok, arg wrong
    assert score(t_multi, "list_todos", {"status": "pending"}) == (1.0, 0.8)  # 1 of 2 args
    assert score(t_acc, "list_todos", {}) == (1.0, 1.0)                       # acceptable alt
    assert score(t_noargs, None, {}) == (0.0, 0.0)                            # no tool chosen
    print("selftest OK")


async def _amain(args):
    _import_gg()
    system = "You are GG. Choose the appropriate available tool for the synthetic request."      # None = bypass live mem0 recall (determinism)
    tool_defs = [t for t in agent._get_all_tool_definitions() if "type" not in t]
    tasks = load_tasks(args.split, args.limit)
    k = args.repeat
    print(f"running {len(tasks)} tasks x{k} = {len(tasks) * k} calls, concurrency={args.concurrency} ...")
    sem = asyncio.Semaphore(args.concurrency)
    jobs = [run_task(t, sem, system, tool_defs) for t in tasks for _ in range(k)]
    raw = await asyncio.gather(*jobs)
    report(aggregate(raw), k)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["train", "val", "test"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--repeat", type=int, default=1, help="samples per task; >1 separates real errors from sampling noise")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
