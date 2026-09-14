"""LLM API 客户端：基于 LangChain ChatOpenAI，支持 DeepSeek/Qwen/OpenAI 兼容端点。"""
import json
import re
from typing import List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from ..utils.logger import get_logger

logger = get_logger(__name__)


class LLMClient:
    """同步调用 LLM 并解析 JSON 输出；由上层负责超时熔断。"""

    def __init__(self, config: dict):
        self.model = config.get("model", "deepseek-chat")
        self.temperature = float(config.get("temperature", 0.1))
        self.timeout = int(config.get("timeout", 5))
        # 底层重试默认关闭：端到端预算由上层 timeout 熔断统一控制，
        # 底层重试会把最长阻塞放大为 timeout*(retries+1)，加剧线程池排队与 P99
        self.max_retries = int(config.get("max_retries", 0))
        self.api_key = config.get("api_key", "")
        self.base_url = config.get("base_url") or None
        self._llm: Optional[ChatOpenAI] = None

    def _get_llm(self) -> ChatOpenAI:
        if self._llm is None:
            kwargs = dict(
                model=self.model,
                temperature=self.temperature,
                timeout=self.timeout,
                max_retries=self.max_retries,
                api_key=self.api_key,
            )
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._llm = ChatOpenAI(**kwargs)
        return self._llm

    def invoke(self, prompt: str) -> str:
        """调用 LLM，返回原始文本（上层解析 JSON，失败时降级）。"""
        response = self._get_llm().invoke([HumanMessage(content=prompt)])
        return response.content

    @staticmethod
    def parse_json(text: str) -> Optional[dict]:
        """从 LLM 输出中提取 JSON 对象，容忍 markdown 代码块包裹。"""
        if not text:
            return None
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
