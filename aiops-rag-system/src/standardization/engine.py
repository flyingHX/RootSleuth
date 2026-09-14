"""标准化引擎核心：Drain 模板提取 -> 规则打分 -> LRU 缓存 -> 热加载。"""
import time
from pathlib import Path
from threading import Event, Thread
from typing import Optional, Tuple

from ..utils.logger import get_logger
from .drain_extractor import DrainExtractor
from .plugin_manager import PluginManager
from .rule_loader import RuleLoader

logger = get_logger(__name__)

_UNCLASSIFIED = "unclassified"
_CONFIDENCE_THRESHOLD = 0.6
_ANY_INCLUDE_SCORE = 10  # any_include 每命中一个关键词的加分
_ERROR_BONUS = 5         # 模板包含 error/fatal 时的加分


class _LRUCache:
    """线程安全的简易 LRU 缓存。"""

    def __init__(self, maxsize: int = 2048):
        from collections import OrderedDict
        self._cache: "OrderedDict" = OrderedDict()
        self._maxsize = maxsize

    def get(self, key):
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def set(self, key, value) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        self._cache.clear()


class StandardizationEngine:
    """将非结构化日志转化为带 error_type 标签的结构化事件，目标耗时 < 10ms。"""

    def __init__(self, config_path: str, drain_config_path: str):
        self.rule_loader = RuleLoader(config_path)
        self._cache = _LRUCache(maxsize=2048)
        # 规则热加载成功后自动清空分类缓存，避免旧结果残留
        self.rule_loader.on_reload = self._cache.clear
        self._drain = DrainExtractor(drain_config_path)
        self._stop_event = Event()
        self._start_watcher()

    # ---------- 1. Drain 模板提取 ----------
    def extract_template(self, raw_message: str) -> str:
        return self._drain.extract(raw_message)

    # ---------- 2. 核心匹配打分 ----------
    def _classify_uncached(self, template: str) -> Tuple[str, float]:
        template_lower = template.lower()
        best_match, best_score = _UNCLASSIFIED, 0

        for rule in self.rule_loader.rules:
            # 无硬匹配条件的规则不参与自动分类（仅允许 must_include / regex 触发）
            if not rule["must_include"] and rule["_compiled_regex"] is None:
                continue
            # 硬约束拦截
            if rule["exclude"] and any(k in template_lower for k in rule["exclude"]):
                continue
            if rule["must_not_include"] and any(k in template_lower for k in rule["must_not_include"]):
                continue

            matched = False
            if rule["must_include"]:
                if not all(k in template_lower for k in rule["must_include"]):
                    continue
                matched = True

            if rule["_compiled_regex"] is not None:
                if rule["_compiled_regex"].search(template):
                    matched = True
                elif not rule["must_include"]:
                    continue

            if not matched:
                continue

            # 规则命中后的基础分取自 rules.yaml 的 score（代表该规则的置信基数）
            score = rule["score"]

            # 软约束加分
            if rule["any_include"]:
                score += sum(1 for k in rule["any_include"] if k in template_lower) * _ANY_INCLUDE_SCORE

            if "error" in template_lower or "fatal" in template_lower:
                score += _ERROR_BONUS

            # 插件化逻辑（返回值即终局判定）
            if rule["handler"]:
                plugin_result = PluginManager.execute(
                    rule["handler"], {"template": template_lower}
                )
                if plugin_result:
                    return plugin_result, 0.99

            if score > best_score:
                best_score = score
                best_match = rule["id"]

        confidence = min(0.99, best_score / 100.0)
        return best_match, confidence

    # ---------- 3. 对外接口 ----------
    def classify(self, raw_message: str, labels: Optional[dict] = None) -> dict:
        template = self.extract_template(raw_message)
        cached = self._cache.get(template)
        if cached is not None:
            error_type, confidence = cached
        else:
            error_type, confidence = self._classify_uncached(template)
            self._cache.set(template, (error_type, confidence))

        return {
            "error_type": error_type,
            "confidence": round(confidence, 2),
            "template": template,
            "is_unknown": error_type == _UNCLASSIFIED or confidence < _CONFIDENCE_THRESHOLD,
        }

    # ---------- 4. 热加载 watcher ----------
    def _start_watcher(self) -> None:
        def watcher():
            while not self._stop_event.is_set():
                self._stop_event.wait(10)
                if self._stop_event.is_set():
                    break
                # check_reload 内部成功后会通过 on_reload 回调清空缓存
                self.rule_loader.check_reload()

        Thread(target=watcher, daemon=True, name="rules-watcher").start()

    def shutdown(self) -> None:
        self._stop_event.set()
