"""地形地平线遮挡（PVGIS API）。

PVGIS 是欧盟 JRC 官方服务：免费、无需注册、对结果用途无限制。
接口 printhorizon 返回「方位角 → 地平线高度角」剖面，单次仅几 KB，
因此不需要下载 DEM（SRTM 30m 单点位需 15~75 MB）。

PVGIS 无覆盖或请求失败时，回退为「直地平线」（不做遮挡判断），
并在结果里标记 considered=False，避免下游误以为已考虑地形。
"""

from . import net

_API = "https://re.jrc.ec.europa.eu/api/v5_3/printhorizon"

# PVGIS 不同版本/口径下的字段别名，做容错解析
_AZ_KEYS = ("azimuth", "A", "az", "Azimuth")
_H_KEYS = ("horizon_height", "H_hor", "horizonHeight", "height", "h")


def fetch_horizon_profile(lat, lon, timeout=None):
    """获取地平线剖面。

    返回 dict：
        profile      [{azimuth, height}, ...]（按方位角升序，0~360）
        considered   True 表示已成功取到地形剖面
        source       'pvgis' | 'flat'
        note         失败原因（considered=False 时）
    """
    try:
        data = net.http_get_json(
            _API,
            params={"lat": round(lat, 5), "lon": round(lon, 5), "outputformat": "json"},
            timeout=timeout,
        )
        rows = _extract_rows(data)
        if not rows:
            return _flat("PVGIS 返回结构无法识别")
        rows.sort(key=lambda r: r["azimuth"])
        return {"profile": rows, "considered": True, "source": "pvgis", "note": None}
    except Exception as e:
        return _flat(str(e))


def _flat(note):
    return {"profile": None, "considered": False, "source": "flat", "note": note}


def _extract_rows(data):
    """从 PVGIS 响应里挖出剖面数组，兼容多种字段命名。"""
    candidates = []
    outputs = data.get("outputs") if isinstance(data, dict) else None
    if isinstance(outputs, dict):
        for v in outputs.values():
            if isinstance(v, list):
                candidates.append(v)
    if isinstance(outputs, list):
        for item in outputs:
            if isinstance(item, dict):
                for v in item.values():
                    if isinstance(v, list):
                        candidates.append(v)
    # 兜底：递归找第一个「元素是含方位角字段的 dict」的列表
    if not candidates:
        candidates = list(_walk_lists(data))

    for lst in candidates:
        rows = []
        for item in lst:
            if not isinstance(item, dict):
                rows = []
                break
            az = _first(item, _AZ_KEYS)
            h = _first(item, _H_KEYS)
            if az is None or h is None:
                rows = []
                break
            try:
                rows.append({"azimuth": float(az) % 360.0, "height": float(h)})
            except (TypeError, ValueError):
                rows = []
                break
        if rows:
            return rows
    return []


def _walk_lists(node):
    if isinstance(node, list):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _walk_lists(v)


def _first(d, keys):
    for k in keys:
        if k in d:
            return d[k]
    return None


def horizon_height_at(profile, azimuth):
    """在剖面上按方位角线性插值，返回地平线高度角（度）。"""
    if not profile:
        return 0.0
    az = azimuth % 360.0
    n = len(profile)
    for i in range(n):
        a0 = profile[i]["azimuth"]
        a1 = profile[(i + 1) % n]["azimuth"]
        span = (a1 - a0) % 360.0
        if span == 0:
            continue
        delta = (az - a0) % 360.0
        if delta <= span:
            h0, h1 = profile[i]["height"], profile[(i + 1) % n]["height"]
            return h0 + (h1 - h0) * (delta / span)
    return profile[0]["height"]


def evaluate_window(profile, considered, samples, band_top=25.0,
                    sample_azimuth=None):
    """评估地形对一个蓝调窗口的遮挡程度。

    注意判据：蓝调时刻太阳本就位于地平线**以下**（−4°~−6°），
    所以「太阳是否被地形挡住」是错误判据（必然判为全遮挡）。
    正确判据是：**朝太阳方向的天空辉光带被地形挡住了多少**。
    辉光带取地平线以上 0° ~ band_top（默认 25°），
    地形地平线高度 H 会遮掉该带最下面的 H 度，故：
        score = clip((band_top - H) / band_top, 0, 1)

    samples: [(时间, 太阳高度角, 太阳方位角), ...]
    sample_azimuth: 若给出，则只按该方位评估（如取窗口中点的方位）
    """
    if not considered or not profile or not samples:
        return {
            "score": 1.0,
            "considered": bool(considered),
            "band_top": band_top,
            "horizon_height": 0.0,
            "blocked_azimuth": None,
            "horizon_height_at_sun": 0.0,
        }

    if sample_azimuth is None:
        az_list = [az for _, _, az in samples]
    else:
        az_list = [sample_azimuth]

    heights = [(horizon_height_at(profile, az), az) for az in az_list]
    h_worst, az_worst = max(heights, key=lambda x: x[0])

    score = (band_top - h_worst) / band_top
    score = max(0.0, min(1.0, score))

    return {
        "score": round(score, 4),
        "considered": True,
        "band_top": band_top,
        "horizon_height": round(h_worst, 2),          # 最不利方位的地平线高度角
        "blocked_azimuth": round(az_worst, 1),
        "horizon_height_at_sun": round(h_worst, 2),
    }
