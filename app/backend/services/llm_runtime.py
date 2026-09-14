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
from services.console_common import get_config

logger = logging.getLogger(__name__)

DEFAULT_LLM_MODEL = "deepseek-v4-flash"

# 配置中心中的密钥类配置（加密存储 + 脱敏展示）
SECRET_CONFIG_KEYS = ("llm_api_key", "embedding_api_key")

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


async def get_llm_settings(db: AsyncSession) -> Dict[str, Any]:
    """读取 LLM 运行时配置（每次实时读库，变更立即生效）。"""
    provider = ((await get_config(db, "llm_provider", "atoms_hub")) or "atoms_hub").strip()
    base_url = (await get_config(db, "llm_base_url", "")).strip()
    api_key = decrypt_secret(await get_config(db, "llm_api_key", ""))
    model = (await get_config(db, "llm_model", DEFAULT_LLM_MODEL)).strip() or DEFAULT_LLM_MODEL
    try:
        temperature = float((await get_config(db, "llm_temperature", "0.2")).strip() or 0.2)
    except ValueError:
        temperature = 0.2
    temperature = min(max(temperature, 0.0), 2.0)
    return {
        "provider": provider,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "temperature": temperature,
    }


async def get_llm_model_name(db: AsyncSession) -> str:
    """轻量接口：仅返回当前 Chat 模型名（用于会话/审计记录）。"""
    return (await get_config(db, "llm_model", DEFAULT_LLM_MODEL)).strip() or DEFAULT_LLM_MODEL


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


async def llm_chat(
    db: AsyncSession,
    messages: List[ChatMessage],
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: int = 1600,
    timeout: Optional[float] = None,
) -> GenTxtResponse:
    """统一 LLM Chat 入口：控制台配置驱动，atoms_hub 回退平台 AIHub。

    超时抛出 asyncio.TimeoutError，降级语义由调用方决定。
    """
    settings_ = await get_llm_settings(db)
    use_model = (model or settings_["model"]).strip()
    use_temperature = settings_["temperature"] if temperature is None else temperature
    if timeout is None:
        try:
            timeout = float((await get_config(db, "llm_timeout_seconds", "45")).strip() or 45)
        except ValueError:
            timeout = 45.0

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


async def test_llm_connectivity(db: AsyncSession) -> Dict[str, Any]:
    """配置连通性自检：最小 Chat 调用 +（若启用）最小 Embedding 调用。"""
    chat: Dict[str, Any] = {}
    started = time.perf_counter()
    try:
        response = await llm_chat(
            db,
            [ChatMessage(role="user", content="连接测试：请只回复 pong")],
            temperature=0.0,
            max_tokens=16,
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
