"""CMDB 资产种子数据：覆盖现有演示告警涉及的全部服务与中间件主机。

重复运行先清空 cmdb_assets 再写入（幂等）。
"""
import asyncio
import json

from sqlalchemy import delete, func, select

from core.database import db_manager
from models.cmdb_assets import Cmdb_assets

CMDB_ASSETS = [
    dict(hostname="api-gw-01", ip="10.10.1.11", system_name="交易网关系统", service_name="api-gateway",
         cluster="prod-cluster-1", environment="prod", owner="张伟", owner_email="zhangwei@atoms.dev",
         dependencies=json.dumps(["payment-service", "order-service"]), log_path="/var/log/api-gateway/access.log",
         status="active", description="南向流量统一入口网关"),
    dict(hostname="pay-svc-01", ip="10.10.2.21", system_name="交易系统", service_name="payment-service",
         cluster="prod-cluster-1", environment="prod", owner="李强", owner_email="liqiang@atoms.dev",
         dependencies=json.dumps(["payment-provider(外部)"]), log_path="/var/log/payment-service/app.log",
         status="active", description="支付核心服务"),
    dict(hostname="pay-svc-02", ip="10.10.2.22", system_name="交易系统", service_name="payment-service",
         cluster="prod-cluster-1", environment="prod", owner="李强", owner_email="liqiang@atoms.dev",
         dependencies=json.dumps(["payment-provider(外部)"]), log_path="/var/log/payment-service/app.log",
         status="active", description="支付核心服务副本"),
    dict(hostname="order-svc-01", ip="10.10.3.31", system_name="交易系统", service_name="order-service",
         cluster="prod-cluster-1", environment="prod", owner="王芳", owner_email="wangfang@atoms.dev",
         dependencies=json.dumps(["redis-cache", "inventory-service"]), log_path="/var/log/order-service/app.log",
         status="active", description="订单服务"),
    dict(hostname="inv-svc-01", ip="10.10.4.41", system_name="库存系统", service_name="inventory-service",
         cluster="prod-cluster-2", environment="prod", owner="赵敏", owner_email="zhaomin@atoms.dev",
         dependencies=json.dumps(["inventory-db"]), log_path="/var/log/inventory-service/app.log",
         status="active", description="库存服务"),
    dict(hostname="inv-db-01", ip="10.10.4.51", system_name="库存系统", service_name="inventory-db",
         cluster="prod-cluster-2", environment="prod", owner="赵敏", owner_email="zhaomin@atoms.dev",
         dependencies=json.dumps([]), log_path="/var/log/postgresql/postgresql.log",
         status="active", description="库存数据库主节点"),
    dict(hostname="user-svc-01", ip="10.10.5.61", system_name="用户系统", service_name="user-service",
         cluster="prod-cluster-1", environment="prod", owner="陈晨", owner_email="chenchen@atoms.dev",
         dependencies=json.dumps(["redis-session"]), log_path="/var/log/user-service/app.log",
         status="active", description="用户与会话服务"),
    dict(hostname="rec-svc-01", ip="10.10.6.71", system_name="推荐系统", service_name="recommendation-service",
         cluster="prod-cluster-2", environment="prod", owner="孙磊", owner_email="sunlei@atoms.dev",
         dependencies=json.dumps(["feature-store"]), log_path="/var/log/recommendation-service/app.log",
         status="active", description="推荐推理服务"),
    dict(hostname="notify-svc-01", ip="10.10.7.81", system_name="通知系统", service_name="notification-service",
         cluster="prod-cluster-2", environment="prod", owner="周婷", owner_email="zhouting@atoms.dev",
         dependencies=json.dumps(["smtp-gateway(外部)", "redis-notify"]), log_path="/var/log/notification-service/app.log",
         status="active", description="消息通知服务"),
    dict(hostname="log-agg-01", ip="10.10.8.91", system_name="日志系统", service_name="log-collector",
         cluster="prod-cluster-1", environment="prod", owner="吴刚", owner_email="wugang@atoms.dev",
         dependencies=json.dumps(["elasticsearch"]), log_path="/var/log/log-collector/collector.log",
         status="active", description="日志采集聚合节点"),
    dict(hostname="redis-cache-01", ip="10.10.9.101", system_name="共享中间件", service_name="redis-cache",
         cluster="prod-cluster-1", environment="prod", owner="郑浩", owner_email="zhenghao@atoms.dev",
         dependencies=json.dumps([]), log_path="/var/log/redis/redis-cache.log",
         status="active", description="业务缓存 Redis"),
    dict(hostname="redis-session-01", ip="10.10.9.102", system_name="共享中间件", service_name="redis-session",
         cluster="prod-cluster-1", environment="prod", owner="郑浩", owner_email="zhenghao@atoms.dev",
         dependencies=json.dumps([]), log_path="/var/log/redis/redis-session.log",
         status="active", description="会话 Redis"),
]


async def main() -> None:
    async with db_manager.session() as db:
        await db.execute(delete(Cmdb_assets))
        await db.commit()
        db.add_all([Cmdb_assets(**asset) for asset in CMDB_ASSETS])
        await db.commit()
        count = (await db.execute(select(func.count()).select_from(Cmdb_assets))).scalar()
        print(f"=== CMDB 种子数据写入完成：{count} 条资产 ===")


if __name__ == "__main__":
    asyncio.run(main())
