"""Copy a real completed run into the repository, replacing machine-specific paths."""

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--output-name", choices=["demo", "ollama-demo", "plugin-demo", "plugin-ollama-demo", "plugin-jaccard-ollama-demo", "plugin-ollama-failure", "repair-ollama-demo", "repair-ollama-initial", "repair-mock-demo", "web-evaluation-demo"], default="demo")
    parser.add_argument("--allow-failure", action="store_true", help="明确归档失败记录，保留真实错误")
    arguments = parser.parse_args()
    if Path(arguments.run_id).name != arguments.run_id:
        raise ValueError("run_id must be a directory name")
    workspace = arguments.workspace.resolve()
    source = workspace / "runs" / arguments.run_id
    report = json.loads((source / "report.json").read_text(encoding="utf-8"))
    if report["status"] != "passed" and not arguments.allow_failure:
        raise ValueError("Only archive a completed successful demo")
    destination = ROOT / "examples" / arguments.output_name
    if destination.exists():
        # Only the repository-owned generated demo is replaced, never user's run history.
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    for path in destination.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            text = text.replace(str(source), f"examples/{arguments.output_name}")
            text = text.replace(str(workspace), ".").replace(str(ROOT), ".")
            path.write_text(text, encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
