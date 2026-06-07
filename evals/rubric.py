"""Rubric management — 版本化的 Judge 评分标准加载与管理.

从 evals/configs/rubric.yaml 加载 rubric 配置，支持版本查询和 prompt 构建。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("evals.rubric")

_CONFIGS_DIR = Path(__file__).resolve().parent / "configs"
_RUBRIC_PATH = _CONFIGS_DIR / "rubric.yaml"

# 模块级缓存
_cached_rubric: dict[str, Any] | None = None


def _load_rubric_yaml() -> dict[str, Any]:
    global _cached_rubric
    if _cached_rubric is not None:
        return _cached_rubric
    if not _RUBRIC_PATH.exists():
        logger.warning("rubric.yaml not found at %s", _RUBRIC_PATH)
        return {}
    with open(_RUBRIC_PATH, encoding="utf-8") as f:
        _cached_rubric = yaml.safe_load(f) or {}
    return _cached_rubric


def reload_rubric() -> None:
    """强制重新加载 rubric 配置（用于运行时更新）."""
    global _cached_rubric
    _cached_rubric = None
    _load_rubric_yaml()


def get_rubric_version() -> str:
    """获取当前 rubric 版本号."""
    data = _load_rubric_yaml()
    return data.get("version", "unknown")


def get_rubric_dimensions() -> list[str]:
    """获取当前 rubric 的评分维度列表."""
    data = _load_rubric_yaml()
    dims = data.get("dimensions", {})
    return list(dims.keys())


def get_dimension_rubric(dimension: str) -> str:
    """获取指定维度的 rubric 文本."""
    data = _load_rubric_yaml()
    dims = data.get("dimensions", {})
    dim_data = dims.get(dimension, {})
    return dim_data.get("rubric", f"{dimension}（0-1分）")


def get_dimension_description(dimension: str) -> str:
    """获取指定维度的简短描述."""
    data = _load_rubric_yaml()
    dims = data.get("dimensions", {})
    dim_data = dims.get(dimension, {})
    return dim_data.get("description", dimension)


def build_judge_prompt(
    *,
    user_query: str,
    scene: str = "未知",
    tool_calls: str = "无",
    recovery_events: int = 0,
    recommendations: str = "无推荐",
    response_text: str = "",
    dimensions: list[str] | None = None,
) -> str:
    """根据 rubric 配置构建 Judge prompt.

    Args:
        user_query: 用户原始请求
        scene: 路由场景
        tool_calls: 工具调用摘要
        recovery_events: 恢复事件数
        recommendations: 推荐内容摘要
        response_text: 回复文本
        dimensions: 评分维度列表（默认使用 rubric.yaml 中的所有维度）

    Returns:
        构建好的 Judge prompt 字符串
    """
    data = _load_rubric_yaml()
    template = data.get("judge_prompt_template", "")
    version = data.get("version", "unknown")

    if dimensions is None:
        dimensions = get_rubric_dimensions()

    # 构建维度 rubric 文本
    dimensions_rubric = ""
    for dim in dimensions:
        desc = get_dimension_description(dim)
        rubric_text = get_dimension_rubric(dim)
        dimensions_rubric += f"### {dim}: {desc}\n{rubric_text}\n\n"

    # 构建 JSON 模板
    dims_json = ", ".join(f'"{d}": 0.8' for d in dimensions)

    return template.format(
        rubric_version=version,
        user_query=user_query,
        scene=scene,
        tool_calls=tool_calls,
        recovery_events=recovery_events,
        recommendations=recommendations,
        response_text=response_text[:1000] if response_text else "",
        dimensions_rubric=dimensions_rubric.strip(),
        dimensions_json_template=dims_json,
    )


def get_full_rubric_config() -> dict[str, Any]:
    """返回完整的 rubric 配置（用于 API 查询）."""
    data = _load_rubric_yaml()
    return {
        "version": data.get("version", "unknown"),
        "updated_at": data.get("updated_at", ""),
        "dimensions": {
            name: {
                "description": dim.get("description", ""),
                "scale": dim.get("scale", [0, 1]),
            }
            for name, dim in data.get("dimensions", {}).items()
        },
    }
