"""存储层测试：ES 缓冲刷盘降级与 Redis 故障 fail-open。"""
from src.storage.es_client import ESClient
from src.storage.redis_client import RedisClient


def test_es_buffer_and_flush_unavailable():
    es = ESClient("http://localhost:9299", index="test-index")  # ES 不可达
    es.write({"event_id": "e1", "fingerprint": "fp"})
    es.write({"event_id": "e2", "fingerprint": "fp"})
    assert len(es._buffer) == 2
    # ES 不可用时 flush 静默丢弃（仅审计场景），不抛异常
    assert es.flush() == 0
    assert es._buffer == []
    es.close()


def test_redis_fail_open_when_unavailable():
    client = RedisClient("redis://localhost:6399/0")  # 不存在的端口
    # Redis 故障时 first_seen 放行（fail-open），避免告警丢失
    assert client.first_seen("fp_x") is True
    assert client.get_topology("svc") is None
    assert client.get_event("evt_missing") is None
    assert client.get_repeat_count("fp_x") == 0
