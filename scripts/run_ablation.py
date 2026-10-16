"""Freeze independent real repair histories, then run paired history comparisons.

Ollama uses fixed seeds and installed model digests. DeepSeek uses unseeded repeat
numbers and a preregistered alternating order; cloud weights remain unverifiable.
Examples (outputs belong in ignored state/ or outside the repository):
  python scripts/run_ablation.py freeze --workspace state/warmup --output state/frozen-history
  python scripts/run_ablation.py run --snapshot state/frozen-history --output state/history-ablation
  python scripts/run_ablation.py run --provider deepseek --repeats 3 --profiles profiles.json --snapshot state/frozen-history --output state/cloud-ablation
Custom profiles are a JSON list with id, task, algorithm, description, requirements.
A frozen history requires real failures followed by verified repairs, never mock injection.
"""

import argparse
import json
from pathlib import Path

from agent_factory.experiments import freeze_history, run_ablation
from agent_factory.providers import load_project_env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze", help="Freeze only independently verified real repair histories")
    freeze.add_argument("--workspace", type=Path, action="append", required=True)
    freeze.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run", help="Run isolated paired on/off experiments and retain every failure")
    run.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    run.add_argument("--snapshot", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--profiles", type=Path)
    sampling = run.add_mutually_exclusive_group()
    sampling.add_argument("--seeds", help="Ollama/mock only; comma-separated unique seeds (default: 11,29)")
    sampling.add_argument("--repeats", type=int, help="DeepSeek only; repeat numbers are not seeds (default: 2)")
    run.add_argument("--provider", choices=["ollama", "mock", "deepseek"], default="ollama")
    run.add_argument("--model", help="Defaults to the selected provider's model/environment setting")
    run.add_argument("--base-url", help="Defaults to the selected provider's endpoint/environment setting")
    run.add_argument("--runner", choices=["docker", "local"], default="docker")
    run.add_argument("--max-repairs", type=int, default=2)
    args = parser.parse_args()
    if args.command == "freeze":
        summary = freeze_history(args.workspace, args.output)
    else:
        if args.provider == "deepseek" and args.seeds is not None:
            parser.error("DeepSeek 不支持固定 seed；请使用 --repeats 指定重复次数。")
        if args.provider != "deepseek" and args.repeats is not None:
            parser.error("--repeats 仅用于 DeepSeek；Ollama/mock 请使用 --seeds。")
        try:
            seeds = tuple(int(value) for value in args.seeds.split(",")) if args.seeds is not None else None
        except ValueError:
            parser.error("--seeds 必须是逗号分隔的整数。")
        load_project_env(args.source)
        profiles = json.loads(args.profiles.read_text(encoding="utf-8")) if args.profiles else None
        summary = run_ablation(args.source, args.snapshot, args.output, profiles,
                               seeds, args.provider, args.model, args.base_url,
                               args.runner, args.max_repairs, repeats=args.repeats)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
