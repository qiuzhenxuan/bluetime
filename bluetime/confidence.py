"""置信度：来自集合预报的云量离散度。

只描述「预报靠不靠谱」，与分数高低无关。
云量均值相同但离散度大时，实际翻车概率高，所以要把离散度直接暴露给用户。
"""

from . import config


def from_spread(spread_pct):
    """spread_pct: 集合成员云量的离散度（百分点）。"""
    if spread_pct is None:
        return {"level": "unknown", "label": "未评估", "spread": None}

    high_max = config.CONFIDENCE["high_max"]
    mid_max = config.CONFIDENCE["mid_max"]
    if spread_pct <= high_max:
        level, label = "high", "高"
    elif spread_pct <= mid_max:
        level, label = "mid", "中"
    else:
        level, label = "low", "低"
    return {"level": level, "label": label, "spread": round(spread_pct, 1)}


def spread_of(values):
    """一组数值的总体标准差（百分点口径）。样本不足 2 个时返回 None。"""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return var ** 0.5
