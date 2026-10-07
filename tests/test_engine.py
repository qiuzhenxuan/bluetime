"""bluetime 引擎单元测试（不联网）。

运行：
    python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bluetime import cache as cache_mod          # noqa: E402
from bluetime import horizon, moon, score, solar  # noqa: E402


class TestSolar(unittest.TestCase):
    """几何引擎精度：与公开星历对照，容差按分钟级要求设定。"""

    def test_beijing_oct_sunrise_sunset(self):
        w = solar.blue_hour_windows(39.9042, 116.4074, date(2026, 10, 7), tz_offset=8)
        sr = w["sunrise"] + __import__("datetime").timedelta(hours=8)
        ss = w["sunset"] + __import__("datetime").timedelta(hours=8)
        # 参考值：北京 2026-10-07 日出约 06:16、日落约 17:48（本地）
        self.assertLess(abs(sr.hour * 60 + sr.minute - (6 * 60 + 16)), 3)
        self.assertLess(abs(ss.hour * 60 + ss.minute - (17 * 60 + 48)), 3)

    def test_equator_equinox_window_about_8min(self):
        """赤道春秋分：蓝调窗口约 8 分钟（文档给值）。"""
        w = solar.blue_hour_windows(0.0, 0.0, date(2026, 3, 20), tz_offset=0)
        self.assertIsNotNone(w["evening"])
        self.assertAlmostEqual(w["evening"]["minutes"], 8.0, delta=1.5)

    def test_high_latitude_summer_window_longer(self):
        """高纬夏季：太阳轨迹平缓，窗口显著拉长。"""
        w = solar.blue_hour_windows(55.0, 10.0, date(2026, 6, 21), tz_offset=2)
        self.assertIsNotNone(w["evening"])
        self.assertGreater(w["evening"]["minutes"], 15.0)

    def test_azimuth_morning_rises_east_evening_sets_west(self):
        w = solar.blue_hour_windows(39.9042, 116.4074, date(2026, 10, 7), tz_offset=8)
        self.assertLess(w["morning"]["mid_azimuth"], 120.0)     # 早晨偏东
        self.assertGreater(w["evening"]["mid_azimuth"], 240.0)  # 傍晚偏西

    def test_solar_noon_altitude_matches_formula(self):
        """正午高度角约等于 90 - |纬度 - 赤纬|。"""
        t = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)   # 北京本地 12:00
        alt, az = solar.solar_position(39.9042, 116.4074, t)
        self.assertAlmostEqual(alt, 44.6, delta=1.0)
        self.assertAlmostEqual(az, 180.0, delta=3.0)

    def test_polar_night_has_no_window(self):
        """极夜：不应给出蓝调窗口。"""
        w = solar.blue_hour_windows(80.0, 0.0, date(2026, 12, 21), tz_offset=0)
        self.assertIsNone(w["morning"])
        self.assertIsNone(w["evening"])


class TestMoon(unittest.TestCase):
    def test_new_moon_low_illumination(self):
        s = moon.moon_state(datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc))
        self.assertLess(s["illumination"], 0.15)

    def test_full_moon_high_illumination(self):
        s = moon.moon_state(datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc))
        self.assertGreater(s["illumination"], 0.85)

    def test_score_inverse_of_illumination(self):
        dt = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
        self.assertAlmostEqual(moon.moon_score(dt),
                               1.0 - moon.moon_state(dt)["illumination"], places=6)


class TestScore(unittest.TestCase):
    def test_clean_sky_scores_high(self):
        r = score.blue_index({
            "cloud_cover": 0, "cloud_cover_low": 0, "cloud_cover_mid": 0,
            "cloud_cover_high": 5, "visibility_km": 25, "aod550": 0.05,
        })
        self.assertGreater(r["score"], 90)
        self.assertIn(r["grade"], ("很蓝", "较蓝"))

    def test_overcast_hazy_scores_low(self):
        r = score.blue_index({
            "cloud_cover": 95, "cloud_cover_low": 90, "cloud_cover_mid": 80,
            "cloud_cover_high": 70, "visibility_km": 3, "aod550": 0.6,
        })
        self.assertLess(r["score"], 15)
        self.assertEqual(r["grade"], "很灰")

    def test_missing_factor_is_renormalized(self):
        """缺少臭氧时应剔除该因子并重新归一化，而不是按 0 分计算。"""
        base = {"cloud_cover": 0, "cloud_cover_low": 0, "cloud_cover_mid": 0,
                "cloud_cover_high": 5, "visibility_km": 25, "aod550": 0.05}
        r = score.blue_index(dict(base))
        self.assertIn("ozone_du", r["missing_factors"])
        self.assertGreater(r["score"], 90)          # 不能被当成 0 分拖低

    def test_quality_uses_blue_index_weight(self):
        q = score.quality_index(blue_score=100, window_minutes=30,
                                terrain_score=1.0, moon_score=1.0, precipitation=0.0)
        self.assertGreater(q["score"], 95)
        q2 = score.quality_index(blue_score=0, window_minutes=30,
                                 terrain_score=1.0, moon_score=1.0, precipitation=0.0)
        self.assertLess(q2["score"], 35)


class TestHorizon(unittest.TestCase):
    PROFILE = [{"azimuth": 0, "height": 0.0}, {"azimuth": 90, "height": 10.0},
               {"azimuth": 180, "height": 0.0}, {"azimuth": 270, "height": 20.0}]

    def test_interpolation(self):
        self.assertAlmostEqual(horizon.horizon_height_at(self.PROFILE, 90), 10.0, places=3)
        self.assertAlmostEqual(horizon.horizon_height_at(self.PROFILE, 45), 5.0, delta=0.5)

    def test_band_logic_uses_sun_azimuth_not_altitude(self):
        """蓝调时太阳在地平线下，判据应是「辉光带被遮多少」而非「太阳是否可见」。"""
        samples = [(None, -5.0, 270.0)]
        r = horizon.evaluate_window(self.PROFILE, True, samples, band_top=25.0)
        # 270° 方向地平线 20°，辉光带 25° → 可见 (25-20)/25 = 20%
        self.assertAlmostEqual(r["score"], 0.2, delta=0.02)
        self.assertAlmostEqual(r["horizon_height"], 20.0, delta=0.5)

    def test_not_considered_returns_full_score(self):
        r = horizon.evaluate_window(None, False, [(None, -5.0, 270.0)])
        self.assertEqual(r["score"], 1.0)


class TestCache(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.cache = cache_mod.Cache(self.path)

    def tearDown(self):
        self.cache.close()
        os.remove(self.path)

    def test_set_and_get(self):
        self.cache.set_json("t", "k", {"v": 1}, ttl=60)
        self.assertEqual(self.cache.get_json("t", "k"), {"v": 1})

    def test_expired_returns_none(self):
        self.cache.set_json("t", "k", {"v": 1}, ttl=-1)
        self.assertIsNone(self.cache.get_json("t", "k"))

    def test_permanent_when_ttl_zero(self):
        self.cache.set_json("t", "k", {"v": 1}, ttl=0)
        self.assertEqual(self.cache.get_json("t", "k"), {"v": 1})

    def test_single_flight(self):
        """并发击穿防护：同一 key 只有一个调用能拿到计算权。"""
        self.assertTrue(self.cache.try_acquire("day", "k", lock_ttl=30))
        self.assertFalse(self.cache.try_acquire("day", "k", lock_ttl=30))
        self.cache.release("day", "k", obj={"ok": True}, ttl=60)
        self.assertEqual(self.cache.get_json("day", "k"), {"ok": True})
        # 已有结果时不应再抢占
        self.assertFalse(self.cache.try_acquire("day", "k", lock_ttl=30))

    def test_expired_lock_can_be_taken_over(self):
        self.assertTrue(self.cache.try_acquire("day", "k2", lock_ttl=-1))
        self.assertTrue(self.cache.try_acquire("day", "k2", lock_ttl=30))

    def test_failed_compute_releases_lock(self):
        self.assertTrue(self.cache.try_acquire("day", "k3", lock_ttl=30))
        self.cache.release("day", "k3", obj=None)
        self.assertTrue(self.cache.try_acquire("day", "k3", lock_ttl=30))


if __name__ == "__main__":
    unittest.main(verbosity=2)
