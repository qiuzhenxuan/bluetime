"""和风天气连通性与字段探测。

在拿到 Key 与 Host 后先跑这个，确认：
    1. 哪个端点可用（和风文档把路径写作 /v7/grid-weather/{hours}，具体取值需实测）
    2. 是否真的返回分层云量 cloudLow / cloudMid / cloudHigh（评分必需）
    3. 各字段的实际命名

认证：本项目用 API KEY，走请求头 X-QW-Api-Key（不再传 key= 查询参数，
     和风会判定为「同时使用多种认证方式」而拒绝）。

用法：
    set QWEATHER_KEY=xxxx
    set QWEATHER_HOST=xxxx.qweatherapi.com
    python tools/probe_qweather.py
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bluetime import net  # noqa: E402

LAT, LON = 24.5917, 118.0083       # 厦门市集美区

CANDIDATES = [
    "/weather/v1/hourly/{lat}/{lon}?hours=72",   # 新 v1（官方主推，1km，全球）
    "/weather/v1/hourly/{lat}/{lon}?hours=24",
    "/v7/weather/72h",                           # 旧 v7（官方已标注「即将弃用」）
    "/v7/weather/24h",
]


def main():
    key = os.environ.get("QWEATHER_KEY", "").strip()
    host = os.environ.get("QWEATHER_HOST", "").strip()
    if not key or not host:
        print("请先设置环境变量 QWEATHER_KEY 与 QWEATHER_HOST")
        print("  Host 在控制台「设置 - API Host」获取，形如 ab12cd.qweatherapi.com")
        return 2

    print(f"Host: {host}   目标点: {LAT}, {LON}（厦门集美区）")
    print("=" * 90)

    for path in CANDIDATES:
        if "{lat}" in path:
            url = "https://" + host.rstrip("/") + path.format(lat=LAT, lon=LON)
            params, note = {}, "新版 v1 接口"
        else:
            url = "https://" + host.rstrip("/") + path
            params = {"location": f"{LON},{LAT}"}
            note = ""
        try:
            d = net.http_get_json(url, params or None, timeout=10,
                                  retries=0, headers={"X-QW-Api-Key": key})
        except Exception as e:
            print(f"[FAIL] {path:<24} {str(e)[:90]}")
            continue

        code = d.get("code")
        hours = d.get("hours") or d.get("hourly") or []
        print(f"[OK]   {path:<24} code={code} 逐小时条数={len(hours)}  {note}")
        if hours:
            first = hours[0]
            keys = sorted(first.keys())
            print(f"       字段：{' '.join(keys)}")
            layered = [k for k in ("cloudLow", "cloudMid", "cloudHigh",
                                   "cloud_low", "cloud_mid", "cloud_high")
                       if k in first]
            print(f"       分层云量：{layered if layered else '❌ 未返回（评分会缺少低/中/高云因子）'}")
            print(f"       样例：{json.dumps({k: first[k] for k in keys[:10]}, ensure_ascii=False)[:300]}")
        print("-" * 90)

    print()
    print("提示：记录下可用的端点与字段名，回填到 bluetime/config.py 的")
    print("      SOURCES['qweather_path']；若字段名不同，改 sources/qweather.py 的 _extract。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
