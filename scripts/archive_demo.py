"""Copy a real completed run into the repository, replacing machine-specific paths."""

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    arguments = parser.parse_args()
    source = ROOT / "runs" / arguments.run_id
    report = json.loads((source / "report.json").read_text(encoding="utf-8"))
    if report["status"] != "passed":
        raise ValueError("Only archive a completed successful demo")
    destination = ROOT / "examples" / "demo"
    if destination.exists():
        # Only the repository-owned generated demo is replaced, never user's run history.
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    for path in destination.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            text = text.replace(str(source), "examples/demo").replace(str(ROOT), ".")
            path.write_text(text, encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
