"""三类 Agent 独立 LLM 接入配置（provider / base_url / api_key）专项测试。

覆盖 services.llm_runtime 的按 Agent 作用域解析语义：
- 留空逐项继承全局 llm_provider / llm_base_url / llm_api_key / llm_model /
  llm_temperature / llm_timeout_seconds（access_source=global）；
- 任一项独立覆盖即按 Agent 生效（access_source=agent），且互不影响其他 Agent；
- diagnose 固定零温语义（diagnose_temperature 默认 0）不被全局温度污染；
- llm_chat 按 Agent 独立接入构造 OpenAI 兼容客户端（base_url/api_key 隔离）；
- test_llm_connectivity 返回 resolved_model / resolved_provider /
  resolved_base_url / resolved_timeout_seconds / access_source（API Key 永不回显）；
- Agent 独立 API Key 加密往返与脱敏；
- 配置中心写入校验（AGENT_PROVIDER_KEYS / AGENT_BASE_URL_KEYS /
  AGENT_ACCESS_API_KEY_KEYS）。

测试通过 monkeypatch 替换 llm_runtime.get_config（读取种子配置字典），
不依赖真实数据库与外部 LLM 网关。
"""
import asyncio
import os
from types import SimpleNamespace

# Fernet 密钥派生依赖：在导入 services 前保证测试进程内可用
os.environ.setdefault("CONSOLE_SECRET_KEY", "test-secret-llm-scope")

from schemas.aihub import ChatMessage  # noqa: E402
from services import llm_runtime  # noqa: E402
from services.console_common import validate_config_value  # noqa: E402
from services.llm_runtime import (  # noqa: E402
    decrypt_secret,
    encrypt_secret,
    get_llm_model_name,
    get_llm_settings,
    get_llm_timeout,
    mask_secret,
)
from services.llm_runtime import test_llm_connectivity as _test_llm_connectivity  # noqa: E402

GLOBAL_CONFIGS = {
    "llm_provider": "atoms_hub",
    "llm_base_url": "https://global.example.com/v1",
    "llm_api_key": encrypt_secret("sk-global-1234567890"),
    "llm_model": "m-global",
    "llm_temperature": "0.3",
    "llm_timeout_seconds": "45",
}


def _patch_config(monkeypatch, extra=None):
    """以全局配置为底、叠加覆盖项，替换 llm_runtime.get_config。"""
    values = dict(GLOBAL_CONFIGS)
    values.update(extra or {})

    async def _fake_get_config(db, key, default=""):
        return values.get(key, default)

    monkeypatch.setattr(llm_runtime, "get_config", _fake_get_config)
    return values


def test_agent_access_inherits_global_when_scoped_empty(monkeypatch):
    """Agent 独立接入配置留空时逐项继承全局（access_source=global）。"""
    _patch_config(
        monkeypatch,
        {"diagnose_llm_provider": "", "diagnose_llm_base_url": "", "diagnose_llm_api_key": ""},
    )
    s = asyncio.run(get_llm_settings(None, agent="diagnose"))
    assert s["provider"] == "atoms_hub"
    assert s["base_url"] == "https://global.example.com/v1"
    assert decrypt_secret(s["api_key"]) == "sk-global-1234567890"
    assert s["model"] == "m-global"
    assert s["access_source"] == "global"
    # diagnose 固定零温语义：空 diagnose_temperature 默认 0，不继承全局 0.3
    assert s["temperature"] == 0.0
    assert asyncio.run(get_llm_timeout(None, "diagnose")) == 45.0
    assert asyncio.run(get_llm_model_name(None, agent="diagnose")) == "m-global"


def test_agent_access_partial_override_and_isolation(monkeypatch):
    """任一项独立覆盖即按 Agent 生效；三个 Agent 互不影响。"""
    _patch_config(
        monkeypatch,
        {
            "diagnose_llm_provider": "openai_compatible",
            "diagnose_llm_base_url": "https://diag.example.com/v1",
            "diagnose_llm_api_key": encrypt_secret("sk-diag-1234567890"),
            "kb_governance_llm_api_key": encrypt_secret("sk-gov-1234567890"),
        },
    )
    diag = asyncio.run(get_llm_settings(None, agent="diagnose"))
    assert diag["provider"] == "openai_compatible"
    assert diag["base_url"] == "https://diag.example.com/v1"
    assert decrypt_secret(diag["api_key"]) == "sk-diag-1234567890"
    assert diag["access_source"] == "agent"

    # kb_governance 仅覆盖 api_key：provider/base_url 仍继承全局（逐项独立覆盖）
    gov = asyncio.run(get_llm_settings(None, agent="kb_governance"))
    assert gov["provider"] == "atoms_hub"
    assert gov["base_url"] == "https://global.example.com/v1"
    assert decrypt_secret(gov["api_key"]) == "sk-gov-1234567890"
    assert gov["access_source"] == "agent"

    # oncall 未配置任何独立项：完全继承全局
    oncall = asyncio.run(get_llm_settings(None, agent="oncall"))
    assert oncall["provider"] == "atoms_hub"
    assert oncall["base_url"] == "https://global.example.com/v1"
    assert decrypt_secret(oncall["api_key"]) == "sk-global-1234567890"
    assert oncall["access_source"] == "global"


def test_agent_model_timeout_temperature_independent(monkeypatch):
    """模型/温度/超时按 Agent 独立解析，未配置的 Agent 继续继承全局。"""
    _patch_config(
        monkeypatch,
        {
            "kb_governance_llm_model": "m-gov",
            "kb_governance_temperature": "1.1",
            "kb_governance_llm_timeout_seconds": "60",
            "diagnose_temperature": "0.4",
        },
    )
    gov_s = asyncio.run(get_llm_settings(None, agent="kb_governance"))
    assert gov_s["model"] == "m-gov"
    assert gov_s["temperature"] == 1.1
    assert asyncio.run(get_llm_timeout(None, "kb_governance")) == 60.0

    oncall_s = asyncio.run(get_llm_settings(None, agent="oncall"))
    assert oncall_s["model"] == "m-global"
    assert oncall_s["temperature"] == 0.3
    assert asyncio.run(get_llm_timeout(None, "oncall")) == 45.0

    # 诊断 Agent：diagnose_temperature=0.4 生效（非全局 0.3、非默认 0）
    diag_s = asyncio.run(get_llm_settings(None, agent="diagnose"))
    assert diag_s["temperature"] == 0.4
    assert diag_s["model"] == "m-global"


def test_llm_chat_uses_agent_specific_openai_client(monkeypatch):
    """llm_chat 按 Agent 独立接入构造 OpenAI 兼容客户端（base_url/api_key 隔离）。"""
    _patch_config(
        monkeypatch,
        {
            "diagnose_llm_provider": "openai_compatible",
            "diagnose_llm_base_url": "https://diag.example.com/v1",
            "diagnose_llm_api_key": encrypt_secret("sk-diag-1234567890"),
            "diagnose_llm_model": "m-diag",
            "diagnose_llm_timeout_seconds": "20",
        },
    )
    sink = SimpleNamespace(created=[], calls=[])

    class _StubCompletions:
        async def create(self, **kwargs):
            sink.calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="pong"))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

    def _fake_get_client(base_url, api_key):
        sink.created.append((base_url, api_key))
        return SimpleNamespace(chat=SimpleNamespace(completions=_StubCompletions()))

    monkeypatch.setattr(llm_runtime, "_get_openai_client", _fake_get_client)

    resp = asyncio.run(
        llm_runtime.llm_chat(None, [ChatMessage(role="user", content="ping")], agent="diagnose")
    )
    assert sink.created == [("https://diag.example.com/v1", "sk-diag-1234567890")]
    assert sink.calls[0]["model"] == "m-diag"
    assert sink.calls[0]["temperature"] == 0.0
    assert resp.content == "pong"


def test_connectivity_reports_agent_resolution_and_hides_api_key(monkeypatch):
    """按 Agent 连通性测试返回继承解析结果；API Key 永不回显。"""
    _patch_config(
        monkeypatch,
        {
            "kb_governance_llm_provider": "openai_compatible",
            "kb_governance_llm_base_url": "https://gov.example.com/v1",
            "kb_governance_llm_model": "m-gov",
            "kb_governance_llm_timeout_seconds": "70",
        },
    )

    async def _fake_llm_chat(db, messages, **kwargs):
        return SimpleNamespace(content="pong", model="m-gov", usage=None)

    monkeypatch.setattr(llm_runtime, "llm_chat", _fake_llm_chat)

    result = asyncio.run(_test_llm_connectivity(None, agent="kb_governance"))
    chat = result["chat"]
    assert chat["ok"] is True
    assert chat["agent"] == "kb_governance"
    assert chat["resolved_model"] == "m-gov"
    assert chat["resolved_timeout_seconds"] == 70.0
    assert chat["resolved_provider"] == "openai_compatible"
    assert chat["resolved_base_url"] == "https://gov.example.com/v1"
    assert chat["access_source"] == "agent"
    # API Key 明文不得出现在连通性测试响应中
    assert not any("sk-" in str(v) for v in chat.values())
    assert result["embedding"]["enabled"] is False

    # 未配置独立项的 oncall：返回全局解析结果
    result2 = asyncio.run(_test_llm_connectivity(None, agent="oncall"))
    assert result2["chat"]["access_source"] == "global"
    assert result2["chat"]["resolved_provider"] == "atoms_hub"
    assert result2["chat"]["resolved_timeout_seconds"] == 45.0


def test_agent_api_key_encrypt_decrypt_roundtrip_and_mask():
    """Agent 独立 API Key：Fernet 加密往返一致，展示一律脱敏。"""
    token = encrypt_secret("sk-agent-secret-0001")
    assert token.startswith("enc:")
    assert decrypt_secret(token) == "sk-agent-secret-0001"
    masked = mask_secret(decrypt_secret(token))
    assert "****" in masked
    assert "sk-agent-secret-0001" not in masked


def test_validate_agent_access_config_keys():
    """配置中心写入校验：Agent 独立 provider/base_url/api_key 的合法与非法值。"""
    # provider：留空 / 合法枚举放行，非法值拒绝
    assert validate_config_value("diagnose_llm_provider", "")[0] is True
    assert validate_config_value("diagnose_llm_provider", "openai_compatible")[0] is True
    assert validate_config_value("diagnose_llm_provider", "bogus")[0] is False
    # base_url：留空 / http(s) 放行，其他协议拒绝
    assert validate_config_value("oncall_llm_base_url", "")[0] is True
    assert validate_config_value("oncall_llm_base_url", "https://x/v1")[0] is True
    assert validate_config_value("oncall_llm_base_url", "ftp://x")[0] is False
    # api_key：完整 Key 放行，脱敏回显值拒绝
    assert validate_config_value("kb_governance_llm_api_key", "sk-real-key-0001")[0] is True
    assert validate_config_value("kb_governance_llm_api_key", "sk-****")[0] is False
