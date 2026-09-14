"""灌入种子知识案例：10 条典型故障 SOP 写入 Milvus 知识库（幂等 upsert）。

用法:
    python scripts/seed_cases.py
"""
import hashlib
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config  # noqa: E402
from src.models.case import KnowledgeCase  # noqa: E402
from src.rag_pipeline.embedder import BGEEmbedder  # noqa: E402
from src.rag_pipeline.milvus_client import MilvusClient  # noqa: E402

SEED_CASES = [
    {
        "service_name": "order-service", "error_type": "redis_timeout",
        "root_cause": "Jedis 连接池 maxTotal=50 过小，大促流量下连接池耗尽，获取连接超时",
        "solution": "1. jedis 配置 maxTotal 调整为 200、maxWait 降为 1s；2. 排查 KEYS/大 Value 慢命令；3. 观察 pool 等待线程数",
        "alert_template": "redis.clients.jedis.exceptions.JedisConnectionException: Could not get a resource from the pool, connection timeout",
        "topology": {"upstream": ["api-gateway"], "downstream": ["redis-cluster"]},
        "resolved_by": "auto",
    },
    {
        "service_name": "cart-service", "error_type": "redis_connection_refused",
        "root_cause": "Redis 主节点宕机且哨兵未完成故障转移，6379 端口拒绝连接",
        "solution": "1. 确认哨兵进程存活并手动触发 failover；2. 检查 redis maxmemory 与内核 somaxconn；3. 恢复后验证主从复制延迟",
        "alert_template": "redis connection refused <IP>:6379, sentinel cluster failover pending",
        "topology": {"upstream": ["cart-web"], "downstream": ["redis-cluster"]},
        "resolved_by": "human",
    },
    {
        "service_name": "order-service", "error_type": "mysql_deadlock",
        "root_cause": "库存扣减与订单创建两个事务以不同顺序更新同一批行锁，形成死锁",
        "solution": "1. 统一按 order_id 升序加锁；2. 缩短事务（拆分非必要更新）；3. 开启 innodb_print_all_deadlocks 观察频率",
        "alert_template": "MySQLTransactionRollbackException: Deadlock found when trying to get lock; try restarting transaction",
        "topology": {"upstream": ["api-gateway"], "downstream": ["mysql-main"]},
        "resolved_by": "human",
    },
    {
        "service_name": "payment-service", "error_type": "mysql_slow_query",
        "root_cause": "对账任务全表扫描（缺 created_time 索引），lock_wait 时间累积拖垮连接池",
        "solution": "1. 为 settlement 表增加 idx_created_time 索引；2. 对账 SQL 增加 limit 分批；3. 慢查询阈值降至 1s 告警",
        "alert_template": "mysql slow query detected, lock_wait <DURATION>, query cost <DURATION>",
        "topology": {"upstream": ["settlement-job"], "downstream": ["mysql-main"]},
        "resolved_by": "human",
    },
    {
        "service_name": "api-gateway", "error_type": "gateway_502",
        "root_cause": "上游 user-service Pod 滚动更新期间未配置优雅停机，连接被重置导致 502",
        "solution": "1. 配置 preStop sleep + readinessGates；2. terminationGracePeriodSeconds 调至 45s；3. nginx upstream max_fails 调整",
        "alert_template": "nginx upstream 502 Bad Gateway, upstream name user-service, connection reset",
        "topology": {"upstream": ["client"], "downstream": ["user-service", "order-service"]},
        "resolved_by": "human",
    },
    {
        "service_name": "order-service", "error_type": "jvm_oom",
        "root_cause": "导出接口一次性加载 50 万行订单到内存，触发 Java heap space OOM",
        "solution": "1. 导出改成分页流式写出；2. 堆参数 -Xmx 调至 4g 并开启 -XX:+HeapDumpOnOutOfMemoryError；3. 添加大查询熔断",
        "alert_template": "java.lang.OutOfMemoryError: Java heap space at OrderExportService.export",
        "topology": {"upstream": ["api-gateway"], "downstream": ["mysql-main"]},
        "resolved_by": "human",
    },
    {
        "service_name": "inventory-service", "error_type": "k8s_oomkill",
        "root_cause": "Pod 内存 limit 512Mi 低于实际峰值（约 700Mi），被内核 OOMKill",
        "solution": "1. resources.limits.memory 调至 1Gi；2. 开启 VPA 建议观察一周；3. 检查是否存在内存泄漏",
        "alert_template": "Pod inventory-service-<NUM> OOMKilled, exit code <NUM>",
        "topology": {"upstream": ["order-service"], "downstream": ["mysql-main"]},
        "resolved_by": "auto",
    },
    {
        "service_name": "search-service", "error_type": "es_cluster_red",
        "root_cause": "磁盘水位超过 90% 触发只读，分片无法分配导致集群 RED",
        "solution": "1. 清理过期索引或扩容数据节点；2. 释放水位后执行 _cluster/reroute 重试分配；3. 配置 ILM 自动滚动删除",
        "alert_template": "elasticsearch cluster health status RED, <NUM> unassigned shards",
        "topology": {"upstream": ["api-gateway"], "downstream": ["es-data-nodes"]},
        "resolved_by": "human",
    },
    {
        "service_name": "stock-service", "error_type": "redis_timeout",
        "root_cause": "热点商品大 Value（1MB）+ KEYS 全量扫描造成 Redis 单线程阻塞",
        "solution": "1. 拆分大 Value 并用 SCAN 替换 KEYS；2. 读写分离将热点 key 迁移至独立实例；3. 客户端增加本地缓存",
        "alert_template": "jedis socket timeout on redis read, pool exhausted, slow command KEYS",
        "topology": {"upstream": ["order-service"], "downstream": ["redis-cluster"]},
        "resolved_by": "human",
    },
    {
        "service_name": "api-gateway", "error_type": "gateway_502",
        "root_cause": "上游 payment-service FullGC 停顿 8s，超过 nginx proxy_read_timeout 被断开",
        "solution": "1. 修复 payment-service 内存泄漏并调优 GC；2. nginx proxy_read_timeout 调至 60s 过渡；3. 增加上游健康检查主动摘流",
        "alert_template": "nginx upstream 502, upstream prematurely closed connection while reading response header",
        "topology": {"upstream": ["client"], "downstream": ["payment-service"]},
        "resolved_by": "human",
    },
]


def build_fingerprint(service_name: str, error_type: str) -> str:
    """与 Webhook 相同的指纹算法：md5(source|service|error_type)。"""
    return hashlib.md5(f"manual|{service_name}|{error_type}".encode()).hexdigest()


def main() -> None:
    config = load_config(str(PROJECT_ROOT))
    milvus = MilvusClient(config["milvus"])
    if not milvus.ensure_collection():
        print("[FAIL] Milvus 集合不可用，请先执行 scripts/init_milvus.py")
        sys.exit(1)

    embedder = BGEEmbedder(config["embedder"])
    now_ms = int(time.time() * 1000)
    ok_count = 0
    for idx, seed in enumerate(SEED_CASES, start=1):
        fingerprint = build_fingerprint(seed["service_name"], seed["error_type"])
        text = (
            f"{seed['alert_template']} {seed['service_name']} "
            f"{seed['error_type']} {seed['root_cause']} {seed['solution']}"
        )
        case = KnowledgeCase(
            case_id=f"seed_{idx:03d}",
            fingerprint=fingerprint,
            service_name=seed["service_name"],
            cluster=seed.get("cluster", "prod"),
            error_type=seed["error_type"],
            severity=seed.get("severity", 2),
            start_time=now_ms - idx * 86400 * 1000,
            root_cause=seed["root_cause"],
            solution=seed["solution"],
            alert_template=seed["alert_template"],
            topology_snapshot=json.dumps(
                seed.get("topology", {"downstream": []}), ensure_ascii=False
            ),
            resolved_by=seed.get("resolved_by", "human"),
            embedding=embedder.embed(text),
            created_at=now_ms,
        )
        if milvus.upsert_case(case.to_milvus_row()):
            ok_count += 1
            print(f"[OK] seed_{idx:03d} {seed['error_type']} -> {seed['service_name']}")
        else:
            print(f"[FAIL] seed_{idx:03d} {seed['error_type']} -> {seed['service_name']}")

    print(f"Seeded {ok_count}/{len(SEED_CASES)} cases into '{config['milvus']['collection']}'.")


if __name__ == "__main__":
    main()
