"""LLM/Embedding 运行时配置：控制台配置中心驱动的模型接入层。

设计：
- 管理员通过配置中心（console_configs）维护 LLM 与 Embedding 的
  provider / base_url / api_key / 模型名 / 温度等参数；
- api_key 使用 Fernet 对称加密持久化（密钥由 CONSOLE_SECRET_KEY 或
  JWT_SECRET_KEY 派生），展示一律脱敏（mask_secret）；
- provider=atoms_hub（默认）时回退平台内置 AIHub（与历史行为一致）；
  provider=openai_compatible 且 base_url/api_key 齐全时使用自建
  OpenAI 兼容客户端（按 (base_url, api_key) 缓存实例）；
- Embedding 独立配置，base_url/api_key 缺省回退 LLM 配置；
- 三个业务 Agent 支持 <agent>_llm_provider / <agent>_llm_base_url /
  <agent>_llm_api_key 独立接入（留空逐项继承全局 llm_*），可单独切换自建网关；
- 所有读取均为每次请求实时读库（异步），配置变更立即生效；
- 自研 ReAct Agent 编排、降级与持久化链路保持不变，仅模型接入
  参数由配置驱动。
"""
import asyncio
import base64
import hashlib
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from schemas.aihub import ChatMessage, GenTxtRequest, GenTxtResponse
from services.aihub import AIHubService
from services.console_common import AGENT_CONFIG_SCOPES, get_config

logger = logging.getLogger(__name__)

DEFAULT_LLM_MODEL = "deepseek-v4-flash"

# 配置中心中的密钥类配置（加密存储 + 脱敏展示）
# 含三个 Agent 的独立 API Key（留空继承全局 llm_api_key）
SECRET_CONFIG_KEYS = (
    "llm_api_key",
    "embedding_api_key",
    "diagnose_llm_api_key",
    "kb_governance_llm_api_key",
    "oncall_llm_api_key",
    "llm_local_api_key",
    "notify_webhook_token",
    "event_ingest_token",
)


def agent_access_keys(agent: str) -> Tuple[str, str, str]:
    """Agent 独立接入配置键名（provider / base_url / api_key，留空逐项继承全局）。"""
    return (f"{agent}_llm_provider", f"{agent}_llm_base_url", f"{agent}_llm_api_key")

try:
    from cryptography.fernet import Fernet
except Exception as exc:  # pragma: no cover - 依赖缺失时给出可诊断错误
    Fernet = None  # type: ignore[assignment]
    _CRYPTO_IMPORT_ERROR: Optional[Exception] = exc
else:
    _CRYPTO_IMPORT_ERROR = None

aihub = AIHubService()

_fernet_instance: Optional[Any] = None
_openai_clients: Dict[Tuple[str, str], Any] = {}


def _get_fernet():
    """按 JWT 密钥派生 Fernet 实例（进程内单例）。"""
    global _fernet_instance
    if Fernet is None:
        raise RuntimeError(f"cryptography 库不可用: {_CRYPTO_IMPORT_ERROR}")
    if _fernet_instance is None:
        secret = os.environ.get("CONSOLE_SECRET_KEY") or os.environ.get("JWT_SECRET_KEY") or ""
        if not secret:
            raise RuntimeError("缺少 CONSOLE_SECRET_KEY / JWT_SECRET_KEY，无法加解密 API Key")
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        _fernet_instance = Fernet(base64.urlsafe_b64encode(digest))
    return _fernet_instance


def encrypt_secret(plain: str) -> str:
    """加密 API Key，持久化格式 enc:<fernet_token>。"""
    token = _get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")
    return f"enc:{token}"


def decrypt_secret(stored: str) -> str:
    """解密 API Key；兼容历史明文与空值；失败返回空串并告警。"""
    stored = (stored or "").strip()
    if not stored:
        return ""
    if not stored.startswith("enc:"):
        return stored
    try:
        return _get_fernet().decrypt(stored[4:].encode("utf-8")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - 密钥轮换等场景保证可诊断
        logger.warning("API Key 解密失败: %s", exc)
        return ""


def mask_secret(plain: str) -> str:
    """API Key 脱敏展示：sk-a****wxyz；短值全掩码。"""
    plain = (plain or "").strip()
    if not plain:
        return ""
    if len(plain) <= 8:
        return "****"
    return f"{plain[:4]}****{plain[-4:]}"


async def get_llm_settings(db: AsyncSession, agent: Optional[str] = None, route: Optional[str] = None) -> Dict[str, Any]:
    """读取 LLM 运行时配置（每次实时读库，变更立即生效）。

    agent 传入三个业务 Agent 作用域（diagnose / kb_governance / oncall）时，
    接入方式与模型参数优先读取 <agent>_* 独立配置（留空逐项继承全局 llm_*）：
    - provider / base_url / api_key：<agent>_llm_provider / <agent>_llm_base_url /
      <agent>_llm_api_key，逐项独立覆盖，支持单个 Agent 切换自建网关；
    - 模型：留空继承全局 llm_model；
    - 温度：kb_governance / oncall 留空或非法继承全局 llm_temperature；
      diagnose 独立语义（diagnose_temperature，默认 0 确定性优先，非法回退 0）。

    route="local" 时强制走本地 LLM（混合路由敏感数据不出域）：provider 固定
    openai_compatible，base_url/api_key/model 取 llm_local_* 配置；本地未配置
    （base_url 或 model 为空）抛 RuntimeError，由路由层保证不会误入此分支。
    """
    provider = ((await get_config(db, "llm_provider", "atoms_hub")) or "atoms_hub").strip()
    base_url = (await get_config(db, "llm_base_url", "")).strip()
    api_key = decrypt_secret(await get_config(db, "llm_api_key", ""))
    model = (await get_config(db, "llm_model", DEFAULT_LLM_MODEL)).strip() or DEFAULT_LLM_MODEL
    global_temperature_raw = (await get_config(db, "llm_temperature", "0.2")).strip() or "0.2"
    try:
        global_temperature = float(global_temperature_raw)
    except ValueError:
        global_temperature = 0.2
    global_temperature = min(max(global_temperature, 0.0), 2.0)
    temperature = global_temperature
    access_source = "global"
    if agent in AGENT_CONFIG_SCOPES:
        # 独立接入（provider/base_url/api_key）：留空逐项继承全局 llm_* 配置，
        # 支持单个 Agent 切换独立网关而不影响其他 Agent 与全局默认
        scoped_provider = (await get_config(db, f"{agent}_llm_provider", "")).strip()
        scoped_base_url = (await get_config(db, f"{agent}_llm_base_url", "")).strip()
        scoped_api_key = decrypt_secret(await get_config(db, f"{agent}_llm_api_key", ""))
        if scoped_provider:
            provider = scoped_provider
            access_source = "agent"
        if scoped_base_url:
            base_url = scoped_base_url
            access_source = "agent"
        if scoped_api_key:
            api_key = scoped_api_key
            access_source = "agent"
        scoped_model = (await get_config(db, f"{agent}_llm_model", "")).strip()
        if scoped_model:
            model = scoped_model
        if agent == "diagnose":
            # 诊断 Agent 固定零温语义（波动治理）：空/非法一律回退 0，而非全局温度
            temperature = _parse_clamped_float(
                (await get_config(db, "diagnose_temperature", "0")).strip(), 0.0, 0.0, 2.0
            )
        else:
            scoped_temperature_raw = (await get_config(db, f"{agent}_temperature", "")).strip()
            if scoped_temperature_raw:
                try:
                    scoped_temperature = float(scoped_temperature_raw)
                except ValueError:
                    logger.warning(
                        "Config %s_temperature=%r 非法，回退全局 llm_temperature", agent, scoped_temperature_raw
                    )
                else:
                    temperature = min(max(scoped_temperature, 0.0), 2.0)
    if route == "local":
        local_base_url = (await get_config(db, "llm_local_base_url", "")).strip()
        local_model = (await get_config(db, "llm_local_model", "")).strip()
        local_api_key = decrypt_secret(await get_config(db, "llm_local_api_key", ""))
        if not (local_base_url and local_model):
            raise RuntimeError("本地 LLM 路由不可用：请先在配置中心填写 llm_local_base_url 与 llm_local_model")
        return {
            "provider": "openai_compatible",
            "base_url": local_base_url,
            "api_key": local_api_key,
            "model": local_model,
            "temperature": temperature,
            "access_source": "local",
        }
    return {
        "provider": provider,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "temperature": temperature,
        "access_source": access_source,
    }


async def get_llm_model_name(db: AsyncSession, agent: Optional[str] = None, route: Optional[str] = None) -> str:
    """轻量接口：仅返回当前 Chat 模型名（用于会话/审计记录）。

    agent 作用域下优先返回 <agent>_llm_model 独立配置，留空回退全局 llm_model；
    route="local" 时返回本地 LLM 模型名（llm_local_model）。
    """
    if route == "local":
        return (await get_config(db, "llm_local_model", "")).strip() or "local-llm"
    if agent in AGENT_CONFIG_SCOPES:
        scoped_model = (await get_config(db, f"{agent}_llm_model", "")).strip()
        if scoped_model:
            return scoped_model
    return (await get_config(db, "llm_model", DEFAULT_LLM_MODEL)).strip() or DEFAULT_LLM_MODEL


async def get_llm_timeout(db: AsyncSession, agent: Optional[str] = None) -> float:
    """读取 LLM 单次调用超时（秒）。

    agent 作用域下优先读取 <agent>_llm_timeout_seconds（10~300 整数），
    留空或非法回退全局 llm_timeout_seconds。
    """
    if agent in AGENT_CONFIG_SCOPES:
        raw = (await get_config(db, f"{agent}_llm_timeout_seconds", "")).strip()
        if raw:
            try:
                return float(int(raw))
            except (TypeError, ValueError):
                logger.warning(
                    "Config %s_llm_timeout_seconds=%r 非法，回退全局 llm_timeout_seconds", agent, raw
                )
    raw = (await get_config(db, "llm_timeout_seconds", "45")).strip() or "45"
    try:
        return float(int(raw))
    except (TypeError, ValueError):
        return 45.0


def _get_openai_client(base_url: str, api_key: str):
    """按 (base_url, api_key) 缓存 OpenAI 兼容客户端实例。"""
    key = (base_url.rstrip("/"), api_key)
    client = _openai_clients.get(key)
    if client is None:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url.rstrip("/"))
        _openai_clients[key] = client
        if len(_openai_clients) > 8:  # 配置频繁变更时防止实例堆积
            _openai_clients.pop(next(iter(_openai_clients)))
    return client


def _parse_clamped_float(raw: Optional[str], default: float, lo: float, hi: float) -> float:
    """宽容解析浮点配置：空/非法/NaN 回退 default，合法值截断到 [lo, hi]。"""
    try:
        value = float(str(raw or "").strip())
    except (TypeError, ValueError):
        return default
    if value != value:  # NaN 防御
        return default
    return min(max(value, lo), hi)


async def llm_chat(
    db: AsyncSession,
    messages: List[ChatMessage],
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: int = 1600,
    timeout: Optional[float] = None,
    agent: Optional[str] = None,
    route: Optional[str] = None,
) -> GenTxtResponse:
    """统一 LLM Chat 入口：控制台配置驱动，atoms_hub 回退平台 AIHub。

    agent 传入业务 Agent 作用域时，模型/温度/超时按 <agent>_* 独立配置解析
    （留空逐项回退全局 llm_* 配置）；显式传入 model/temperature/timeout 仍然优先。
    route="local" 时强制走本地 LLM（llm_local_* 配置，混合路由敏感数据不出域），
    本地未配置抛 RuntimeError；路由决策由 services/llm_routing.py 统一做出。
    超时抛出 asyncio.TimeoutError，降级语义由调用方决定。
    """
    settings_ = await get_llm_settings(db, agent=agent, route=route)
    use_model = (model or settings_["model"]).strip()
    use_temperature = settings_["temperature"] if temperature is None else temperature
    if timeout is None:
        timeout = await get_llm_timeout(db, agent)

    if settings_["provider"] == "openai_compatible" and settings_["base_url"] and settings_["api_key"]:
        client = _get_openai_client(settings_["base_url"], settings_["api_key"])
        payload = [{"role": m.role, "content": m.content} for m in messages]
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=use_model,
                messages=payload,
                temperature=use_temperature,
                max_tokens=max_tokens,
                stream=False,
            ),
            timeout=timeout,
        )
        content = ""
        choices = getattr(response, "choices", None)
        if choices:
            content = getattr(getattr(choices[0], "message", None), "content", None) or ""
        usage = None
        resp_usage = getattr(response, "usage", None)
        if resp_usage:
            usage = {
                "prompt_tokens": getattr(resp_usage, "prompt_tokens", None),
                "completion_tokens": getattr(resp_usage, "completion_tokens", None),
                "total_tokens": getattr(resp_usage, "total_tokens", None),
            }
        return GenTxtResponse(content=content, model=use_model, usage=usage)

    # 平台内置 AIHub（默认回退，行为与历史版本一致）
    request = GenTxtRequest(
        model=use_model,
        messages=list(messages),
        temperature=use_temperature,
        max_tokens=max_tokens,
    )
    return await asyncio.wait_for(aihub.gentxt(request), timeout=timeout)


async def get_embedding_settings(db: AsyncSession) -> Optional[Dict[str, Any]]:
    """读取 Embedding 配置；url/key/model 不完整时返回 None。

    base_url / api_key 缺省回退 LLM 配置。
    """
    base_url = (await get_config(db, "embedding_base_url", "")).strip()
    api_key = decrypt_secret(await get_config(db, "embedding_api_key", ""))
    if not base_url:
        base_url = (await get_config(db, "llm_base_url", "")).strip()
    if not api_key:
        api_key = decrypt_secret(await get_config(db, "llm_api_key", ""))
    model = (await get_config(db, "embedding_model", "")).strip()
    if not (base_url and api_key and model):
        return None
    return {"base_url": base_url, "api_key": api_key, "model": model}


async def embed_texts(
    db: AsyncSession,
    texts: List[str],
    *,
    timeout: float = 30.0,
    raise_on_error: bool = False,
) -> Optional[List[List[float]]]:
    """OpenAI 兼容 embeddings 批量调用；未启用返回 None，失败按参数决定。"""
    settings_ = await get_embedding_settings(db)
    if not settings_:
        return None
    try:
        client = _get_openai_client(settings_["base_url"], settings_["api_key"])
        response = await asyncio.wait_for(
            client.embeddings.create(model=settings_["model"], input=texts),
            timeout=timeout,
        )
        vectors = [list(item.embedding) for item in response.data]
        if len(vectors) != len(texts):
            raise ValueError(f"embeddings 返回数量不匹配: {len(vectors)}/{len(texts)}")
        return vectors
    except Exception as exc:  # noqa: BLE001 - Embedding 失败不应阻断诊断主链路
        if raise_on_error:
            raise
        logger.warning("Embedding 调用失败（降级忽略）: %s", exc)
        return None


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """余弦相似度（零向量/维度不一致返回 0）。"""
    import math

    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def test_llm_connectivity(db: AsyncSession, agent: Optional[str] = None) -> Dict[str, Any]:
    """配置连通性自检：最小 Chat 调用 +（若启用）最小 Embedding 调用。

    agent 传入三个业务 Agent 作用域时按 <agent>_* 独立配置测试（留空项回退全局）。
    """
    chat: Dict[str, Any] = {}
    started = time.perf_counter()
    try:
        response = await llm_chat(
            db,
            [ChatMessage(role="user", content="连接测试：请只回复 pong")],
            temperature=0.0,
            max_tokens=16,
            agent=agent,
        )
        chat = {
            "ok": True,
            "model": response.model,
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "sample": (response.content or "").strip()[:80],
        }
    except Exception as exc:  # noqa: BLE001 - 测试端点必须把错误带给管理员
        chat = {
            "ok": False,
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }
    if agent:
        chat["agent"] = agent
        # 展示该 Agent 实际生效的模型/超时/接入（含继承解析结果），便于管理员核对；
        # base_url 非敏感可直接回显，API Key 永不回显
        chat["resolved_model"] = await get_llm_model_name(db, agent=agent)
        chat["resolved_timeout_seconds"] = await get_llm_timeout(db, agent)
        resolved_settings = await get_llm_settings(db, agent=agent)
        chat["resolved_provider"] = resolved_settings["provider"]
        chat["resolved_base_url"] = resolved_settings["base_url"]
        chat["access_source"] = resolved_settings["access_source"]

    embedding: Dict[str, Any]
    emb_settings = await get_embedding_settings(db)
    if emb_settings is None:
        embedding = {
            "enabled": False,
            "note": "未启用：需配置 embedding_model（base_url/api_key 缺省回退 LLM 配置）",
        }
    else:
        started = time.perf_counter()
        try:
            vectors = await embed_texts(db, ["connectivity test"], raise_on_error=True)
            embedding = {
                "enabled": True,
                "ok": bool(vectors and vectors[0]),
                "model": emb_settings["model"],
                "dims": len(vectors[0]) if vectors and vectors[0] else None,
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
            }
        except Exception as exc:  # noqa: BLE001
            embedding = {
                "enabled": True,
                "ok": False,
                "model": emb_settings["model"],
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
                "error": f"{type(exc).__name__}: {exc}"[:300],
            }
    return {"chat": chat, "embedding": embedding}
