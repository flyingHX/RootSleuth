"""RAG 服务间鉴权与租户身份（P0：服务间认证 + 多租户隔离）。

设计：
- 调用方以 `X-API-Key`（或 `Authorization: Bearer <key>`）表明服务身份；
- Key -> 身份（name / tenant_id / scopes）映射由 `RAG_API_KEYS_JSON` 配置：
  {"<key>": {"name": "console", "tenant_id": "default", "scopes": ["read", "write"]}}
- scopes：read=检索/验证/观测（kb-search、kb-sync/verify、kb-cache/status、diagnostic），
  write=写路径（kb-sync upsert/delete、kb-cache/invalidate、feedback、cases/close）；
- 未配置任何 Key（或 RAG_AUTH_ENABLED=false）-> 鉴权关闭（本地开发/存量测试兼容），
  请求按匿名 default 租户放行；配置 Key 后未携带/错误 Key -> 401，缺 scope -> 403。

租户语义（越权召回率 0% 的口径）：
- 数据行按 tenant_id 标注；"default" 为公共知识层；
- 租户 t 的可见集合 = {default, t}；default 租户仅可见 default；
  任意两个非 default 租户互相不可见 -> 跨租户越权召回为 0；
- 旧 Milvus 集合缺失 tenant_id 字段时（Schema 交集兼容）跳过租户过滤并留日志；
  新建集合默认启用（见 milvus_client._SCHEMA_FIELDS）。
"""
import json
from dataclasses import dataclass
from typing import List, Optional, Tuple

from fastapi import Header, HTTPException

from ..runtime import get_config
from ..utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_TENANT = "default"


@dataclass(frozen=True)
class ServiceIdentity:
    """通过鉴权的服务调用方身份（authenticated=False 表示鉴权未启用的匿名放行）。"""

    name: str = "anonymous"
    tenant_id: str = DEFAULT_TENANT
    scopes: Tuple[str, ...] = ("read", "write")
    authenticated: bool = False


# ------------------ 配置解析 ------------------

def _auth_settings() -> dict:
    """读取鉴权配置；runtime 未初始化（部分单测环境）时按未启用处理。"""
    try:
        return get_config().get("auth") or {}
    except RuntimeError:
        return {}


def _api_keys(settings: dict) -> dict:
    """解析 RAG_API_KEYS_JSON（兼容直接传 dict，便于测试注入）。"""
    raw = settings.get("api_keys_json")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("RAG_API_KEYS_JSON is not valid JSON, auth disabled")
        return {}
    return parsed if isinstance(parsed, dict) else {}


def auth_enabled() -> bool:
    """鉴权开关：显式关闭或未配置任何 Key 时返回 False（fail-open，兼容存量部署）。"""
    settings = _auth_settings()
    if str(settings.get("enabled", "true")).lower() in ("0", "false", "no"):
        return False
    return bool(_api_keys(settings))


def require_auth(*required_scopes: str):
    """FastAPI 依赖工厂：校验 API Key 与 scopes，返回 ServiceIdentity。"""

    def dependency(
        x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
        authorization: Optional[str] = Header(default=None),
    ) -> ServiceIdentity:
        if not auth_enabled():
            return ServiceIdentity()  # 匿名 default 租户（鉴权关闭的存量语义）
        token = x_api_key
        if not token and authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        keys = _api_keys(_auth_settings())
        if not token or token not in keys:
            raise HTTPException(
                status_code=401,
                detail={"error": "unauthorized", "message": "missing or invalid X-API-Key"},
            )
        entry = keys.get(token) or {}
        if isinstance(entry, str):  # 允许简写：{"<key>": "console"}
            entry = {"name": entry}
        granted = tuple(entry.get("scopes") or ["read", "write"])
        missing = [scope for scope in required_scopes if scope not in granted]
        if missing:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "scope_denied",
                    "required": list(required_scopes),
                    "missing": missing,
                },
            )
        return ServiceIdentity(
            name=str(entry.get("name") or "service"),
            tenant_id=str(entry.get("tenant_id") or DEFAULT_TENANT),
            scopes=granted,
            authenticated=True,
        )

    return dependency


# ------------------ 租户可见性 ------------------
# 实现移至 utils/tenant.py（供 rag_pipeline 数据层复用且避免循环导入），此处 re-export 保持 API 兼容。
from ..utils.tenant import DEFAULT_TENANT, row_visible, tenant_expr, tenant_visible_values  # noqa: E402,F401
