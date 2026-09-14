"""特殊逻辑插件管理器：注册/执行规则级自定义 handler。"""
import time
from collections import defaultdict
from typing import Callable, Dict, Optional


class PluginManager:
    _handlers: Dict[str, Callable] = {}

    @classmethod
    def register(cls, name: str) -> Callable:
        def decorator(func: Callable) -> Callable:
            cls._handlers[name] = func
            return func
        return decorator

    @classmethod
    def execute(cls, name: str, context: dict) -> Optional[str]:
        func = cls._handlers.get(name)
        if func is None:
            return None
        try:
            return func(context)
        except Exception:  # noqa: BLE001
            return None


_burst_counter: Dict[str, list] = defaultdict(list)
_BURST_WINDOW_SECONDS = 60
_BURST_THRESHOLD = 100


@PluginManager.register("burst_detector")
def detect_burst(context: dict) -> Optional[str]:
    """滑动窗口频率检测：60 秒内同一模板出现超过阈值判定为突发错误。"""
    template = context.get("template", "")
    now = time.time()
    _burst_counter[template] = [t for t in _burst_counter[template] if now - t < _BURST_WINDOW_SECONDS]
    _burst_counter[template].append(now)
    if len(_burst_counter[template]) > _BURST_THRESHOLD:
        return "burst_error"
    return None
