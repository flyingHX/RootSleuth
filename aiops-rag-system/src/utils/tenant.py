"""租户与环境可见性纯函数（P0-2 多租户隔离）。

独立于 api/security.py（鉴权），供 rag_pipeline 数据层复用且避免循环导入：
- 数据行按 tenant_id 标注，"default" 为公共知识层；
- 租户 t 的可见集合 = {default, t}；default 租户仅可见 default；
  任意两个非 default 租户互相不可见 -> 跨租户越权召回为 0；
- 数据行按 environment 标注部署环境（prod/staging/...）；检索请求显式提供
  environment 时做等值过滤（环境间知识隔离），缺省不过滤（存量调用兼容）；
- 旧 Milvus 集合缺失 tenant_id/environment 字段时（Schema 交集兼容）由调用方跳过过滤。
"""
from typing import List, Optional

DEFAULT_TENANT = "default"
DEFAULT_ENVIRONMENT = ""


def tenant_visible_values(tenant_id: str) -> List[str]:
    """调用方租户可见的 tenant_id 取值集合：default 租户仅看公共层，其余租户看公共层+自身。"""
    tenant = tenant_id or DEFAULT_TENANT
    return [DEFAULT_TENANT] if tenant == DEFAULT_TENANT else [DEFAULT_TENANT, tenant]


def row_visible(row_tenant: Optional[str], caller_tenant: str) -> bool:
    """数据行租户对调用方是否可见（旧数据缺 tenant_id 视为 default 公共层）。"""
    return str(row_tenant or DEFAULT_TENANT) in tenant_visible_values(caller_tenant)


def tenant_expr(tenant_id: str) -> str:
    """Milvus 布尔表达式片段：tenant_id in [...]（供检索/删除 expr 拼接）。"""
    values = ",".join(f'"{_quote_safe(v)}"' for v in tenant_visible_values(tenant_id))
    return f"tenant_id in [{values}]"


def environment_expr(environment: str) -> str:
    """Milvus 布尔表达式片段：环境过滤，含空环境公共/存量层（P0-2）。

    查询 env=prod 时可见 environment == "prod" 与 environment == ""（未标注环境的
    存量知识按共享层处理，避免迁移期召回塌陷）；查询未指定环境时由调用方跳过过滤。
    """
    return f'environment in ["{_quote_safe(environment)}", ""]'


def row_environment_visible(row_environment: Optional[str], query_environment: Optional[str]) -> bool:
    """数据行环境对查询是否可见：查询未指定环境时全量可见（存量兼容）。

    查询指定环境时：行环境与查询环境等值，或行未标注环境（""，公共/存量共享层）。
    """
    if not query_environment:
        return True
    row_env = str(row_environment or "")
    return row_env == str(query_environment) or row_env == ""


def _quote_safe(value: str) -> str:
    """Milvus 表达式字符串转义：剔除内嵌引号与反斜杠，避免表达式注入。"""
    return str(value or "").replace('"', "").replace("\\", "")
