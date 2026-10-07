"""输出格式化：文本（给人看）与 JSON（给小程序后端用）。"""


def _fmt(v, unit="", nd=None):
    if v is None:
        return "—"
    if nd is not None:
        v = round(v, nd)
    return f"{v}{unit}"


def format_text(result):
    """把 compute_day 的结果渲染成可读文本。"""
    lines = []
    tz = result.get("tz_offset", 0)
    tz_label = f"UTC{tz:+g}"
    lines.append(f"地点  {result['lat']:.4f}, {result['lon']:.4f}   时区 {tz_label}")
    lines.append(f"日期  {result['local_date']}")

    ctx = result.get("context") or {}
    lines.append("")
    lines.append(f"日出 {ctx.get('sunrise') or '—'}   日落 {ctx.get('sunset') or '—'}"
                 f"   民用暮光结束 {ctx.get('civil_dusk') or '—'}")
    elev = result.get("elevation_m")
    if elev is None:
        lines.append("海拔 未知（日出/日落未做地平线下沉修正）")
    else:
        lines.append(f"海拔 {elev:g} m（地平线下沉 {result.get('horizon_dip_deg', 0):.2f}°，"
                     f"已修正日出/日落；蓝调窗口不受影响）")

    for name, label in (("morning", "早晨蓝调"), ("evening", "傍晚蓝调")):
        w = result["windows"].get(name)
        lines.append("")
        lines.append(f"【{label}】")
        if not w:
            lines.append("  无蓝调窗口（极昼/极夜或高纬夏季）")
            continue
        lines.append(f"  {w['start_local']} → {w['end_local']}   窗口 {w['minutes']} 分钟")
        lines.append(f"  太阳方位 {w['sun_azimuth']}°   高度 {w['sun_altitude']}°")

        t = w["terrain"]
        if t.get("considered"):
            h = t.get("horizon_height") or 0.0
            if h < 1.0:
                lines.append(f"  地形遮挡 轻微（{t.get('blocked_azimuth')}° 方向地平线 {h}°）")
            else:
                lines.append(f"  地形遮挡 {t.get('blocked_azimuth')}° 方向地平线 {h}°，"
                             f"辉光带（0~{t.get('band_top')}°）可见比例 "
                             f"{round((t.get('score') or 0) * 100)}%")
        else:
            lines.append(f"  地形遮挡 未生效（{t.get('note') or '未启用'}）")

        bi, q = w["blue_index"], w["quality"]
        lines.append(f"  蓝度指数   {bi['score']:5.1f} / 100   {bi['grade']}")
        lines.append(f"  综合质量分 {q['score']:5.1f} / 100   {q['grade']}")

        wx = w["weather"]
        lines.append("  因子明细  " + "  ".join([
            f"总云{_fmt(wx['cloud_cover'], '%', 0)}",
            f"低云{_fmt(wx['cloud_cover_low'], '%', 0)}",
            f"中云{_fmt(wx['cloud_cover_mid'], '%', 0)}",
            f"高云{_fmt(wx['cloud_cover_high'], '%', 0)}",
            f"能见度{_fmt(wx['visibility_km'], 'km', 1)}",
            f"AOD{_fmt(wx['aod550'], '', 2)}",
            f"臭氧{_fmt(wx['ozone_du'], 'DU', 0)}",
            f"湿度{_fmt(wx['relative_humidity'], '%', 0)}",
            f"降水{_fmt(wx['precipitation'], 'mm', 1)}",
        ]))
        lines.append(f"  月相      {w['moon']['phase_name']}"
                     f"（照明 {round(w['moon']['illumination'] * 100)}%）")

    if result.get("warnings"):
        lines.append("")
        lines.append("提示")
        for w in result["warnings"]:
            lines.append(f"  · {w}")

    lines.append("")
    lines.append(f"说明：评分权重为初始估计，尚未用实测数据标定；"
                 f"气象数据来源 {result.get('weather_source') or '—'}。")
    return "\n".join(lines)
