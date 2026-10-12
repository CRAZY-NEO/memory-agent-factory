"""Tests isolate all knowledge databases, traces and artifacts in temporary workspaces."""

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace(tmp_path):
    for directory in ["data", "knowledge"]:
        shutil.copytree(ROOT / directory, tmp_path / directory)
    shutil.copyfile(ROOT / "config.json", tmp_path / "config.json")
    return tmp_path
