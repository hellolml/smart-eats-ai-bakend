"""pytest 评测专用 fixtures."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# 确保项目根目录在 sys.path 中
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from evals.datasets.eval_case import Category, EvalCase, Scene


@pytest.fixture(scope="session")
def dataset_dir() -> Path:
    """数据集目录"""
    return project_root / "evals" / "datasets"


@pytest.fixture(scope="session")
def all_cases(dataset_dir: Path) -> list[EvalCase]:
    """加载所有评测用例"""
    cases: list[EvalCase] = []
    for json_file in dataset_dir.glob("*.json"):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for item in data:
                    cases.append(EvalCase.from_dict(item))
        except Exception:
            pass
    return cases


@pytest.fixture(scope="session")
def normal_cases(all_cases: list[EvalCase]) -> list[EvalCase]:
    """正常用例"""
    return [c for c in all_cases if c.category == Category.NORMAL]


@pytest.fixture(scope="session")
def failure_cases(all_cases: list[EvalCase]) -> list[EvalCase]:
    """工具失败用例"""
    return [c for c in all_cases if c.category == Category.TOOL_FAILURE]


@pytest.fixture(scope="session")
def safety_cases(all_cases: list[EvalCase]) -> list[EvalCase]:
    """安全用例"""
    return [c for c in all_cases if c.category == Category.SAFETY]


@pytest.fixture(scope="session")
def boundary_cases(all_cases: list[EvalCase]) -> list[EvalCase]:
    """边界用例"""
    return [c for c in all_cases if c.category == Category.BOUNDARY]


@pytest.fixture(scope="session")
def eval_base_url() -> str:
    """评测后端 URL"""
    return "http://127.0.0.1:8000"
