"""Drain 模板提取器：变量归一为占位符，命中正则缓存直接返回。"""
import re
from pathlib import Path
from typing import List

import yaml

from ..utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_PATTERNS: List[dict] = [
    {"regex": r"\b\d{1,3}(\.\d{1,3}){3}(:\d+)?", "replace": "<IP>"},
    {"regex": r"\b\d+(\.\d+)?(ms|s)\b", "replace": "<DURATION>"},
    {"regex": r"\b\d+\b", "replace": "<NUM>"},
]

_TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}(?:\.\d+)?")
_THREAD_RE = re.compile(r"\[[^\]]*\]")


class DrainExtractor:
    """轻量 Drain 风格模板提取：配置化变量替换 + 通用时间/线程归一。"""

    def __init__(self, drain_config_path: str):
        self._compiled: List[tuple] = []
        self._load(drain_config_path)

    def _load(self, drain_config_path: str) -> None:
        patterns: List[dict] = []
        path = Path(drain_config_path)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    patterns = (yaml.safe_load(f) or {}).get("patterns", [])
            except Exception as exc:  # noqa: BLE001
                logger.error("Drain config load failed, use defaults: %s", exc)
                patterns = []
        self._compiled = []
        for p in patterns:
            try:
                self._compiled.append((re.compile(p["regex"], re.IGNORECASE), p["replace"]))
            except re.error as exc:
                logger.error("Invalid drain regex %s: %s", p.get("regex"), exc)

    def extract(self, raw_message: str) -> str:
        """将原始日志归一为模板（变量 -> 占位符）。

        顺序：先做内置时间/线程归一（防止 <NUM> 等通用规则破坏时间戳），
        再执行配置化变量替换（IP/UUID/HASH/时长/数字等）。
        """
        template = _TIME_RE.sub("<TIME>", raw_message)
        template = _THREAD_RE.sub("<THREAD>", template)
        for regex, replace in self._compiled:
            template = regex.sub(replace, template)
        return template.strip()
