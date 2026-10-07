"""命令行入口（本地调试与验证用）。

示例：
    python -m bluetime.cli --lat 39.9042 --lng 116.4074 --date 2026-10-07
    python -m bluetime.cli --lat 39.9042 --lng 116.4074 --days 3 --json
    python -m bluetime.cli --lat 39.9042 --lng 116.4074 --no-terrain --no-cache
"""

import argparse
import json
import sys
from datetime import date, timedelta

from . import cache as cache_mod
from . import pipeline, report, solar


def build_parser():
    p = argparse.ArgumentParser(
        prog="bluetime",
        description="蓝调时刻评分引擎：输出指定地点的蓝调窗口与双指标评分")
    p.add_argument("--lat", type=float, required=True, help="纬度（南纬为负）")
    p.add_argument("--lng", "--lon", dest="lng", type=float, required=True,
                   help="经度（西经为负）")
    p.add_argument("--date", type=str, default=None,
                   help="本地日期 YYYY-MM-DD，默认今天")
    p.add_argument("--days", type=int, default=1, help="连续计算天数，默认 1")
    p.add_argument("--tz", type=float, default=None,
                   help="本地时区相对 UTC 的小时数，默认按经度推算")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.add_argument("--no-terrain", action="store_true", help="跳过地形遮挡（PVGIS）")
    p.add_argument("--no-cache", action="store_true", help="禁用缓存")
    p.add_argument("--day-cache", action="store_true",
                   help="走「首个访客计算并缓存」策略（同 get_or_compute）")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    tz = (args.tz if args.tz is not None
          else solar.local_timezone_offset(args.lng, args.lat))
    base = (date.fromisoformat(args.date) if args.date else date.today())

    cache = None if args.no_cache else cache_mod.Cache()
    out = []
    try:
        for i in range(max(1, args.days)):
            d = base + timedelta(days=i)
            if args.day_cache:
                r, meta = pipeline.get_or_compute(
                    args.lat, args.lng, d, tz_offset=tz,
                    with_terrain=not args.no_terrain, cache=cache)
                if r is None:
                    print(f"{d} 正在被其他请求计算（computing=True），请稍后重试")
                    continue
                r["_cache"] = meta
                out.append(r)
            else:
                out.append(pipeline.compute_day(
                    args.lat, args.lng, d,
                    tz_offset=tz,
                    with_terrain=not args.no_terrain,
                    cache=cache,
                ))
    finally:
        if cache is not None:
            cache.close()

    if args.json:
        json.dump(out if args.days > 1 else out[0],
                  sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        for r in out:
            print(report.format_text(r))
            if len(out) > 1:
                print("-" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
