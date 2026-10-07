"""算法可行性验证（二）：与 Sunsethue 对照（秩相关 + 窗口时刻）。

Sunsethue 是商业化的 ray-based 日落质量模型，有公开白皮书与免费额度，
可作为「外部参照」。注意两者评的不是同一现象：

    本项目  = 蓝调时刻（太阳 −4°~−6°）的「天空是否纯净通透」
    Sunsethue = 日落时刻（金色时刻附近）的「云能否接住阳光」

机制不同，因此**秩相关系数不应期望很高**（0.4~0.6 即视为方向一致）。
但 `magics.blue_hour` 的**起止时刻**与本项目可直接对比，这是硬指标。

用法：
    set SUNSETHUE_API_KEY=xxxx
    python tools/validate_vs_sunsethue.py            # 默认集美区
    python tools/validate_vs_sunsethue.py --days 7
    python tools/validate_vs_sunsethue.py --lat 39.9042 --lng 116.4074
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bluetime import cache as cache_mod  # noqa: E402
from bluetime import net, pipeline, solar  # noqa: E402

# 厦门市集美区：官方范围 北纬24°25′~24°46′、东经117°57′~118°04′，取几何中心
JIMEI = (24.5917, 118.0083, 8, "厦门市集美区")

SUNSETHUE_URL = "https://api.sunsethue.com/event"


def fetch_sunsethue(lat, lon, d, key, event="sunset"):
    """调用 Sunsethue。鉴权方式为 x-api-key 请求头。"""
    return net.http_get_json(
        SUNSETHUE_URL,
        {"latitude": lat, "longitude": lon, "date": str(d), "type": event},
        timeout=15, retries=0, headers={"x-api-key": key})


def spearman(pairs):
    """计算 Spearman 秩相关（含并列值取平均秩）。stdlib 实现。"""
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    n = len(pairs)
    if n < 3:
        return None, n

    def ranks(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx = ranks([p[0] for p in pairs])
    ry = ranks([p[1] for p in pairs])
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    if dx == 0 or dy == 0:
        return None, n
    return num / (dx * dy), n


def main():
    p = argparse.ArgumentParser(description="与 Sunsethue 对照验证")
    p.add_argument("--lat", type=float, default=JIMEI[0])
    p.add_argument("--lng", type=float, default=JIMEI[1])
    p.add_argument("--tz", type=float, default=JIMEI[2])
    p.add_argument("--name", default=JIMEI[3])
    p.add_argument("--start", default=None, help="起始本地日期，默认今天")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--no-terrain", action="store_true")
    args = p.parse_args()

    key = os.environ.get("SUNSETHUE_API_KEY", "").strip()
    if not key:
        print("缺少 SUNSETHUE_API_KEY 环境变量")
        return 2

    base = date.fromisoformat(args.start) if args.start else date.today()
    cache = cache_mod.Cache()
    rows, pairs = [], []

    print(f"地点：{args.name}  ({args.lat}, {args.lng})  时区 UTC{args.tz:+g}")
    print(f"日期范围：{base} 起 {args.days} 天")
    print("=" * 100)
    print(f"{'日期':<12}{'本报蓝调窗口(口径-6~-4°)':<26}{'Sunsethue蓝调窗口(含其高度角)':<40}"
          f"{'本方蓝度':>8}{'本方综合':>8}{'其日落质量':>10}")
    print("-" * 100)

    try:
        for i in range(args.days):
            d = base + timedelta(days=i)
            try:
                ours = pipeline.compute_day(args.lat, args.lng, d, tz_offset=args.tz,
                                            with_terrain=not args.no_terrain,
                                            cache=cache)
            except Exception as e:
                print(f"{str(d):<12}本方计算失败：{e}")
                continue

            ev = ours["windows"].get("evening")
            our_win = f"{ev['start_local'][11:16]}-{ev['end_local'][11:16]}" if ev else "无"
            blue = ev["blue_index"]["score"] if ev else None
            qual = ev["quality"]["score"] if ev else None

            their_win, their_q, their_txt = "—", None, ""
            try:
                r = fetch_sunsethue(args.lat, args.lng, d, key)
                data = r.get("data", {})
                bh = (data.get("magics") or {}).get("blue_hour") or []
                if len(bh) == 2:
                    # Sunsethue 返回 UTC ISO（形如 ...T10:06:00.000Z），换算成本地时间
                    def hhmm(s):
                        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                        return (dt + timedelta(hours=args.tz)).strftime("%H:%M")

                    # 顺带反推对方窗口对应的高度角区间——用于排查「口径差异」
                    def alt_at(s):
                        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                        a, _ = solar.solar_position(args.lat, args.lng,
                                                    dt.astimezone(timezone.utc))
                        return a
                    their_win = (f"{hhmm(bh[0])}-{hhmm(bh[1])}"
                                 f" ({alt_at(bh[0]):.1f}°~{alt_at(bh[1]):.1f}°)")
                their_q = data.get("quality")
                their_txt = data.get("quality_text", "")
            except Exception as e:
                their_win = "取数失败"

            if their_q is not None and blue is not None:
                pairs.append((blue, their_q * 100.0))

            print(f"{str(d):<12}{our_win:<20}{their_win:<20}"
                  f"{(blue if blue is not None else -1):>8.1f}"
                  f"{(qual if qual is not None else -1):>8.1f}"
                  f"{(their_q * 100 if their_q is not None else -1):>10.1f}"
                  f"  {their_txt}")
    finally:
        cache.close()

    rho, n = spearman(pairs)
    print("-" * 100)
    if rho is None:
        print(f"样本不足（有效配对 {n} 个），无法计算秩相关。")
    else:
        print(f"Spearman 秩相关（本方蓝度指数 vs Sunsethue 日落质量）：rho = {rho:.3f}（n={n}）")
        print("判读：rho > 0.6 方向高度一致；0.4~0.6 方向一致；< 0.2 基本无关。")
        print("注意：两者评的不是同一现象（蓝调 vs 日落），且样本少时波动大。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
