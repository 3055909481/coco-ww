"""在线更新在 GitHub API 限流时的降级路径测试（离线，不依赖网络）。

在仓库根目录跑：python -X utf8 tests\\test_update_fallback.py
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyappify  # noqa: E402


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestRateLimitFallback(unittest.TestCase):
    def test_fallback_reads_raw_version(self):
        """API 限流时应从 raw version.txt 构造出可用的最新版条目。"""
        with mock.patch.object(pyappify, "_get_json",
                               side_effect=RuntimeError("GitHub 接口访问太频繁被限流了，过一小时再试")), \
             mock.patch.object(pyappify, "_open",
                               return_value=FakeResponse(b"v9.9.9\n")):
            releases = pyappify._fetch_releases()
        self.assertIn("v9.9.9", releases)
        entry = releases["v9.9.9"]
        self.assertIn("update-manifest.json", entry["manifest_url"])
        self.assertIn("releases/download/v9.9.9", entry["manifest_url"])
        self.assertFalse(entry["prerelease"])

    def test_non_ratelimit_error_not_swallowed(self):
        """非限流错误（如网络断开）不能被降级逻辑吞掉。"""
        with mock.patch.object(pyappify, "_get_json",
                               side_effect=RuntimeError("连不上 GitHub（国内网络需要自己挂代理）")):
            with self.assertRaises(RuntimeError) as ctx:
                pyappify._fetch_releases()
        self.assertIn("连不上 GitHub", str(ctx.exception))

    def test_get_version_list_survives_rate_limit(self):
        """get_version_list 在限流下仍能返回版本列表（截图里报错的那个入口）。"""
        with mock.patch.object(pyappify, "_get_json",
                               side_effect=RuntimeError("GitHub 接口访问太频繁被限流了")), \
             mock.patch.object(pyappify, "_open",
                               return_value=FakeResponse(b"v1.0.6\n")):
            versions = pyappify.get_version_list(release_only=True)
        self.assertEqual([v["version"] for v in versions], ["v1.0.6"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
