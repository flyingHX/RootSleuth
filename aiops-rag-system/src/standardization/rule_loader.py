"""rules.yaml 热加载器：文件 mtime 监听 + 内存快照兜底。"""
import re
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

from ..utils.logger import get_logger

logger = get_logger(__name__)

_VALID_KEYS = {"id", "priority", "conditions", "score", "handler"}
_COND_KEYS = {"must_include", "any_include", "exclude", "must_not_include", "regex"}


class RuleLoader:
    """加载并规范化 rules.yaml，支持热加载与坏配置兜底。"""

    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self._last_mtime: float = 0.0
        self.rules: List[dict] = []
        self.on_reload: Optional[callable] = None  # 热加载成功后的回调（如清空上游缓存）
        self.load()

    def load(self) -> bool:
        """加载规则文件，成功返回 True；文件损坏时保留内存快照返回 False。"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            raw_rules = data.get("rules", [])
            new_rules = [self._normalize(item) for item in raw_rules]
            new_rules.sort(key=lambda x: x["priority"])
            self.rules = new_rules
            self._last_mtime = self.config_path.stat().st_mtime
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Rule load failed, keep last snapshot: %s", exc)
            return False

    def check_reload(self) -> bool:
        """mtime 变化时热加载并清空上层缓存；返回是否发生了重载。"""
        try:
            if self.config_path.stat().st_mtime != self._last_mtime:
                ok = self.load()
                if ok and self.on_reload:
                    self.on_reload()
                logger.info("Rules hot reloaded (ok=%s), mtime=%s", ok, self._last_mtime)
                return ok
        except OSError:
            pass
        return False

    @staticmethod
    def _normalize(item: dict) -> dict:
        unknown = set(item) - _VALID_KEYS
        if unknown:
            logger.warning("Rule %s has unknown keys: %s", item.get("id"), unknown)
        cond = {
            k: v for k, v in (item.get("conditions") or {}).items() if k in _COND_KEYS
        }
        rule: dict = {
            "id": item["id"],
            "priority": item.get("priority", 99),
            "must_include": [k.lower() for k in cond.get("must_include", [])],
            "any_include": [k.lower() for k in cond.get("any_include", [])],
            "exclude": [k.lower() for k in cond.get("exclude", [])],
            "must_not_include": [k.lower() for k in cond.get("must_not_include", [])],
            "score": item.get("score", 60),
            "handler": item.get("handler"),
            "_compiled_regex": None,
        }
        regex = cond.get("regex")
        if regex:
            try:
                rule["_compiled_regex"] = re.compile(regex, re.IGNORECASE)
            except re.error as exc:
                logger.error("Invalid regex in rule %s: %s (%s)", rule["id"], regex, exc)
        return rule
