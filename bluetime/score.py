"""评分引擎（经验权重规则，纯计算、不联网）。

双指标：
    蓝度指数   —— 天空够不够蓝、够不够纯（只看颜色质量）
    综合质量分 —— 这次蓝调值不值得专门去拍（叠加时长/地形/月相/降水）

权重与阈值全部来自 config，便于后续用实测数据标定。
缺失的因子会被自动剔除并重新归一化权重，避免「缺数据反而抬高分数」。
"""

import math

from . import config


def _membership(value, spec):
    """把因子原始值映射到 [0,1] 隶属度。"""
    if value is None:
        return None
    d = spec["dir"]
    if d == "neg":
        good, bad = spec["good"], spec["bad"]
        if value <= good:
            return 1.0
        if value >= bad:
            return 0.0
        return (bad - value) / (bad - good)
    if d == "pos":
        bad, good = spec["bad"], spec["good"]
        if value >= good:
            return 1.0
        if value <= bad:
            return 0.0
        return (value - bad) / (good - bad)
    if d == "trap":
        a, b, c, dd = spec["a"], spec["b"], spec["c"], spec["d"]
        if value <= a or value >= dd:
            return 0.0
        if b <= value <= c:
            return 1.0
        if value < b:
            return (value - a) / (b - a)
        return (dd - value) / (dd - c)
    raise ValueError(f"未知的隶属度方向：{d}")


def _weighted(values, weights, thresholds, aggregation="geometric", floor=0.02):
    """聚合成 0~100 分。

    geometric: 100 × ∏(s_i ^ ŵ_i)，ŵ 为在**可用因子**上重新归一化后的权重。
        语义是「必要条件」——任一要素极差则整体极差，不会被其他要素补偿。
    arithmetic: 100 × Σ(ŵ_i · s_i)，允许因子互相补偿。

    缺失因子会被剔除并重新分配权重，避免「缺数据反而抬高分数」。
    floor 为隶属度下限，防止几何平均出现 0 悬崖。
    """
    memberships = {}
    factors = {}
    missing = []
    total_w = 0.0

    for name, w in weights.items():
        raw = values.get(name)
        mem = _membership(raw, thresholds[name]) if name in thresholds else None
        if mem is None:
            missing.append(name)
            factors[name] = {"value": None, "score": None, "weight": w,
                             "effective_weight": 0.0, "contribution": 0.0}
            continue
        memberships[name] = mem
        total_w += w
        factors[name] = {"value": raw, "score": round(mem, 4), "weight": w,
                         "effective_weight": 0.0, "contribution": 0.0}

    if total_w == 0:
        return 0.0, factors, missing

    if aggregation == "geometric":
        acc = 0.0
        for name, mem in memberships.items():
            eff = weights[name] / total_w
            acc += eff * math.log(max(floor, mem))
            factors[name]["effective_weight"] = round(eff, 4)
            factors[name]["contribution"] = round(eff * max(floor, mem), 4)
        score = 100.0 * math.exp(acc)
    else:
        acc = 0.0
        for name, mem in memberships.items():
            eff = weights[name] / total_w
            acc += eff * mem
            factors[name]["effective_weight"] = round(eff, 4)
            factors[name]["contribution"] = round(eff * mem, 4)
        score = 100.0 * acc

    return score, factors, missing


def grade(score, kind="blue"):
    """按分数返回分级文字。"""
    key = "blue" if kind == "blue" else "quality"
    for g in config.GRADES:
        if score >= g["min"]:
            return g[key]
    return config.GRADES[-1][key]


def blue_index(values):
    """蓝度指数（0~100）。values 为因子原始值字典。"""
    score, factors, missing = _weighted(
        values, config.BLUE_INDEX["weights"], config.BLUE_INDEX["thresholds"],
        config.BLUE_INDEX.get("aggregation", "geometric"),
        config.BLUE_INDEX.get("floor", 0.02))
    return {
        "score": round(score, 1),
        "grade": grade(score, "blue"),
        "factors": factors,
        "missing_factors": missing,
    }


def quality_index(blue_score, window_minutes, terrain_score, moon_score, precipitation):
    """综合质量分（0~100）。"""
    values = {
        "blue_index": blue_score,
        "window_minutes": window_minutes,
        "terrain_score": terrain_score,
        "moon_score": moon_score,
        "precipitation": precipitation,
    }
    score, factors, missing = _weighted(
        values, config.QUALITY["weights"], config.QUALITY["thresholds"],
        config.QUALITY.get("aggregation", "geometric"),
        config.QUALITY.get("floor", 0.02))
    return {
        "score": round(score, 1),
        "grade": grade(score, "quality"),
        "factors": factors,
        "missing_factors": missing,
    }


def evaluate(factor_values, window_minutes, terrain_score, moon_score, precipitation):
    """一次性算出双指标。"""
    blue = blue_index(factor_values)
    qual = quality_index(blue["score"], window_minutes, terrain_score,
                         moon_score, precipitation)
    return {"blue_index": blue, "quality": qual}
