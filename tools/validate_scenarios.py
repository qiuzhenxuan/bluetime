"""算法可行性验证（一）：极端场景自检。

目的：在没有真实评分标签的情况下，先用「已知物理直觉」检验评分方向是否单调合理。
如果连沙尘暴/暴雨都拿到高分，那说明权重或隶属度写反了。

用法：
    python tools/validate_scenarios.py

退出码：0 = 全部通过；1 = 存在违反直觉的评分。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bluetime import score  # noqa: E402

# 每个场景：名称、(因子原始值, 期望等级区间)
# 因子字段与 score.blue_index 的 config 一致；ozone_du 缺省表示未接 CAMS
SCENARIOS = [
    {
        "name": "理想蓝调（万里无云、极通透）",
        "factors": {"cloud_cover": 0, "cloud_cover_low": 0, "cloud_cover_mid": 0,
                    "cloud_cover_high": 5, "visibility_km": 30, "aod550": 0.04},
        "expect": ("很蓝", "较蓝"),
        "why": "云量近乎为零 + 能见度 30km + AOD 0.04，应是最纯净的蓝",
    },
    {
        "name": "雨后晴空",
        "factors": {"cloud_cover": 10, "cloud_cover_low": 5, "cloud_cover_mid": 5,
                    "cloud_cover_high": 15, "visibility_km": 25, "aod550": 0.06},
        "expect": ("很蓝", "较蓝"),
        "why": "雨后空气洗净，AOD 低、能见度高",
    },
    {
        "name": "薄卷云（高云多、低中云干净）",
        "factors": {"cloud_cover": 35, "cloud_cover_low": 0, "cloud_cover_mid": 10,
                    "cloud_cover_high": 60, "visibility_km": 25, "aod550": 0.07},
        "expect": ("很蓝", "较蓝", "一般"),
        "why": "高云只轻微减纯，低中云干净，仍应偏蓝",
    },
    {
        "name": "典型灰霾",
        "factors": {"cloud_cover": 15, "cloud_cover_low": 10, "cloud_cover_mid": 10,
                    "cloud_cover_high": 20, "visibility_km": 5, "aod550": 0.40},
        "expect": ("一般", "偏灰", "很灰"),
        "why": "云虽少，但 AOD 0.4 + 能见度仅 5km，天空发灰",
    },
    {
        "name": "浓云密布（三层云都满）",
        "factors": {"cloud_cover": 100, "cloud_cover_low": 100, "cloud_cover_mid": 95,
                    "cloud_cover_high": 80, "visibility_km": 10, "aod550": 0.20},
        "expect": ("偏灰", "很灰"),
        "why": "云把天光全遮了，蓝调无从谈起",
    },
    {
        "name": "沙尘暴（AOD 爆表）",
        "factors": {"cloud_cover": 20, "cloud_cover_low": 15, "cloud_cover_mid": 10,
                    "cloud_cover_high": 20, "visibility_km": 1, "aod550": 1.20},
        "expect": ("偏灰", "很灰"),
        "why": "沙尘让天空浑浊发黄，对蓝调是灾难（注意：对晚霞反而可能增色）",
    },
    {
        "name": "暴雨中",
        "factors": {"cloud_cover": 100, "cloud_cover_low": 100, "cloud_cover_mid": 90,
                    "cloud_cover_high": 70, "visibility_km": 3, "aod550": 0.50},
        "expect": ("很灰",),
        "why": "云量满 + 能见度低 + 降水，无观赏价值",
    },
]

# 期望的单调序（从左到右分数不增）
# 说明：前两项都会饱和到 100，故用「不增」而非「严格递减」。
# 「浓云密布」低于「沙尘暴」是符合物理的：前者连天光都没有，后者还能看到浑浊的光。
EXPECTED_ORDER = [
    "理想蓝调（万里无云、极通透）",
    "雨后晴空",
    "薄卷云（高云多、低中云干净）",
    "典型灰霾",
    "沙尘暴（AOD 爆表）",
    "浓云密布（三层云都满）",
    "暴雨中",
]


def main():
    print("=" * 78)
    print("算法可行性验证（一）：极端场景自检")
    print("=" * 78)
    print()

    results = {}
    ok = True

    for sc in SCENARIOS:
        r = score.blue_index(dict(sc["factors"]))
        results[sc["name"]] = r
        hit = r["grade"] in sc["expect"]
        ok = ok and hit
        flag = "OK " if hit else "!! "
        print(f"{flag}{sc['name']}")
        print(f"     蓝度指数 {r['score']:5.1f}  等级「{r['grade']}」 "
              f"期望「{' / '.join(sc['expect'])}」")
        print(f"     依据：{sc['why']}")
        if r["missing_factors"]:
            print(f"     缺失因子（已剔除并重新归一化）：{', '.join(r['missing_factors'])}")
        print()

    print("-" * 78)
    print("单调性检查（期望分数不增）")
    print("-" * 78)
    prev, prev_name = None, None
    for name in EXPECTED_ORDER:
        s = results[name]["score"]
        if prev is not None:
            monotonic = s <= prev + 1e-9
            mark = "OK " if monotonic else "!! "
            print(f"{mark}{prev_name}({prev:.1f})  >=  {name}({s:.1f})")
            ok = ok and monotonic
        prev, prev_name = s, name
    print()

    # 沙尘暴 vs 典型灰霾：沙尘应更低（能见度与 AOD 都更差）
    sand = results["沙尘暴（AOD 爆表）"]["score"]
    haze = results["典型灰霾"]["score"]
    cond = sand < haze
    ok = ok and cond
    print(f"{'OK ' if cond else '!! '}沙尘暴({sand:.1f}) 应低于 典型灰霾({haze:.1f})：{cond}")
    print()

    print("=" * 78)
    print("结论：" + ("全部通过，评分方向符合物理直觉" if ok
                    else "存在违反直觉的评分，需要检查权重与隶属度"))
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
