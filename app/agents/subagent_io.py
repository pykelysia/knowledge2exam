"""SubAgent 文件读写工具。

所有 SubAgent 将结果写入 storage/jobs/{job_id}/subagent/ 目录。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import settings


def get_subagent_dir(job_id: str | int) -> Path:
    """获取 subagent 输出目录。"""
    job_dir = Path(settings.storage_dir) / "jobs" / str(job_id) / "subagent"
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def write_subagent_result(
    job_id: str | int,
    seq: int,
    result_type: str,
    data: dict[str, Any],
) -> Path:
    """写入 subagent 结果文件。

    Args:
        job_id: 任务 ID
        seq: 序号
        result_type: 结果类型（plan/question/review）
        data: 要写入的数据

    Returns:
        写入的文件路径
    """
    output_dir = get_subagent_dir(job_id)
    filename = f"{result_type}_{seq:03d}.json"
    file_path = output_dir / filename

    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return file_path


def read_subagent_result(
    job_id: str | int,
    seq: int,
    result_type: str,
) -> dict[str, Any] | None:
    """读取 subagent 结果文件。

    Args:
        job_id: 任务 ID
        seq: 序号
        result_type: 结果类型（plan/question/review）

    Returns:
        文件内容，如果文件不存在则返回 None
    """
    output_dir = get_subagent_dir(job_id)
    filename = f"{result_type}_{seq:03d}.json"
    file_path = output_dir / filename

    if not file_path.exists():
        return None

    return json.loads(file_path.read_text())


def read_all_subagent_results(
    job_id: str | int,
    result_type: str,
) -> list[dict[str, Any]]:
    """读取指定类型的所有 subagent 结果文件（按 seq 排序）。

    Args:
        job_id: 任务 ID
        result_type: 结果类型（plan/question/review）

    Returns:
        文件内容列表（按 seq 排序）
    """
    output_dir = get_subagent_dir(job_id)
    pattern = f"{result_type}_*.json"

    results = []
    for file_path in sorted(output_dir.glob(pattern)):
        try:
            data = json.loads(file_path.read_text())
            results.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    return results
