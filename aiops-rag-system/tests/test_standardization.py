"""标准化引擎测试：规则匹配、Drain 模板提取、LRU 缓存、热加载与插件。"""
from pathlib import Path

import pytest

from src.standardization.drain_extractor import DrainExtractor
from src.standardization.engine import StandardizationEngine
from src.standardization.plugin_manager import PluginManager

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = PROJECT_ROOT / "config" / "rules.yaml"
DRAIN_PATH = PROJECT_ROOT / "config" / "drain_patterns.yaml"


@pytest.fixture(scope="module")
def engine():
    eng = StandardizationEngine(str(RULES_PATH), str(DRAIN_PATH))
    yield eng
    eng.shutdown()


class TestClassify:
    def test_redis_timeout(self, engine):
        result = engine.classify(
            "redis.clients.jedis.exceptions.JedisConnectionException: "
            "Could not get a resource from the pool, connection timeout after 3000ms"
        )
        assert result["error_type"] == "redis_timeout"
        assert result["confidence"] >= 0.6
        assert result["is_unknown"] is False

    def test_mysql_deadlock_regex(self, engine):
        result = engine.classify(
            "MySQLTransactionRollbackException: Deadlock found when trying to get lock; "
            "try restarting transaction"
        )
        assert result["error_type"] == "mysql_deadlock"
        assert result["confidence"] >= 0.9

    def test_k8s_oomkill(self, engine):
        result = engine.classify("Pod inventory-service-7d9f OOMKilled, exit code 137")
        assert result["error_type"] == "k8s_oomkill"

    def test_gateway_502(self, engine):
        result = engine.classify("nginx upstream 502 Bad Gateway while reading response header")
        assert result["error_type"] == "gateway_502"

    def test_unknown_log(self, engine):
        result = engine.classify("disk usage on /data partition is high")
        assert result["error_type"] == "unclassified"
        assert result["is_unknown"] is True

    def test_exclude_condition(self, engine):
        # 含 sentinel 的 jedis timeout 应命中排除条件，落回 unclassified
        result = engine.classify(
            "jedis timeout with sentinel mode switch, pool waiting"
        )
        assert result["error_type"] != "redis_timeout"


class TestDrainAndCache:
    def test_extract_template_basic(self, engine):
        template = engine.extract_template(
            "2026-09-11 10:00:00 ERROR [http-nio-8080-exec-1] connect failed"
        )
        assert "<TIME>" in template
        assert "<THREAD>" in template
        assert "ERROR" in template

    def test_lru_cache_hit(self, engine):
        msg = "jedis connection timeout, pool exhausted for cache test"
        first = engine.classify(msg)
        second = engine.classify(msg)
        assert first == second
        cached = engine._cache.get(engine.extract_template(msg))
        assert cached is not None


class TestHotReload:
    def test_rule_hot_reload(self, tmp_path):
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "rules:\n"
            "  - id: test_rule_a\n"
            "    priority: 1\n"
            "    conditions:\n"
            "      must_include: [\"foo\"]\n"
            "    score: 90\n",
            encoding="utf-8",
        )
        drain_file = tmp_path / "drain.yaml"
        drain_file.write_text("patterns: []\n", encoding="utf-8")

        eng = StandardizationEngine(str(rules_file), str(drain_file))
        try:
            assert eng.classify("foo bar")["error_type"] == "test_rule_a"

            # 修改规则文件并显式推进 mtime，触发热加载
            import os

            rules_file.write_text(
                "rules:\n"
                "  - id: test_rule_b\n"
                "    priority: 1\n"
                "    conditions:\n"
                "      must_include: [\"foo\"]\n"
                "    score: 90\n",
                encoding="utf-8",
            )
            st = rules_file.stat()
            os.utime(rules_file, (st.st_atime + 5, st.st_mtime + 5))
            assert eng.rule_loader.check_reload() is True
            assert eng.classify("foo bar")["error_type"] == "test_rule_b"
        finally:
            eng.shutdown()

    def test_bad_config_keeps_snapshot(self, tmp_path):
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "rules:\n  - id: keep_me\n    priority: 1\n    conditions:\n      must_include: [\"ok\"]\n    score: 90\n",
            encoding="utf-8",
        )
        drain_file = tmp_path / "drain.yaml"
        drain_file.write_text("patterns: []\n", encoding="utf-8")

        loader = __import__("src.standardization.rule_loader", fromlist=["RuleLoader"]).RuleLoader(
            str(rules_file)
        )
        assert loader.rules[0]["id"] == "keep_me"

        # 写入坏配置 -> 加载失败时应保留内存快照
        rules_file.write_text("rules: [ { broken yaml !!!", encoding="utf-8")
        assert loader.check_reload() is False
        assert loader.rules[0]["id"] == "keep_me"


class TestPlugin:
    def test_burst_detector_threshold(self):
        result = None
        for _ in range(150):
            result = PluginManager.execute("burst_detector", {"template": "spam-template-x"})
        assert result == "burst_error"

    def test_unknown_handler_returns_none(self):
        assert PluginManager.execute("no_such_handler", {"template": "x"}) is None


class TestDrainExtractor:
    def test_variable_replacement(self, tmp_path):
        drain_file = tmp_path / "drain.yaml"
        drain_file.write_text(
            "patterns:\n"
            "  - regex: '\\d{4}-\\d{2}-\\d{2}[\\sT]\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?'\n"
            "    replace: '<TIME>'\n"
            "  - regex: '\\b\\d{1,3}(\\.\\d{1,3}){3}(:\\d+)?'\n"
            "    replace: '<IP>'\n"
            "  - regex: '\\b\\d+(\\.\\d+)?(ms|s)\\b'\n"
            "    replace: '<DURATION>'\n"
            "  - regex: '\\b\\d+\\b'\n"
            "    replace: '<NUM>'\n",
            encoding="utf-8",
        )
        extractor = DrainExtractor(str(drain_file))
        template = extractor.extract(
            "2026-09-11 10:00:00 ERROR [http-nio-8080-exec-1] "
            "connect to 10.0.0.1:6379 timeout after 3000ms"
        )
        assert template == "<TIME> ERROR <THREAD> connect to <IP> timeout after <DURATION>"
