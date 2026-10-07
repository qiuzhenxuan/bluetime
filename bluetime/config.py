"""bluetime 引擎配置。

改权重/阈值只改这里，不动代码。用 Python 模块而非 YAML，
是为了保持「零第三方依赖」——云函数包更小、冷启动更快。

密钥（和风 API Key / Host）从环境变量读取，不写进代码，避免泄露：
    set QWEATHER_KEY=xxxx
    set QWEATHER_HOST=xxxx.qweatherapi.com
未设置时自动回落到 Open-Meteo（免费、无需 Key）。
"""

import os

# 蓝调窗口定义（太阳中心高度角，单位：度）
# 口径依据：2026-10-07 以厦门集美区实测反推 Sunsethue 的窗口对应 -5.1°~-7.4°，
# 本口径取 -5.0°~-7.5° 与之对齐（便于跨产品对照）。
# 注意：摄影业界另一主流口径是 -4°~-6°（sunrise-sunset.org、PhotoPills）。
# 换口径会同时改变窗口起止与时长，跨产品对比前必须先归一化。
BLUE_HOUR = {
    "core_min": -7.5,   # 核心窗口下限（更暗的一端）
    "core_max": -5.0,   # 核心窗口上限（更亮的一端）
}

# 蓝度指数：因子权重与阈值
# dir: neg = 越小越好；pos = 越大越好；trap = 区间型
# neg: x <= good 得 1 分，x >= bad 得 0 分；pos 反之
# trap: a..b 上升，b..c 平坦(=1)，c..d 下降（a,b,c,d 为梯形四点）
BLUE_INDEX = {
    # 聚合方式：
    #   geometric  加权几何平均（推荐）——「必要条件」语义，任一要素极差则整体极差，
    #              不会被其他要素的满分补偿（例如沙尘天云少但天空浑浊，不应得高分）
    #   arithmetic 加权算术平均——允许因子互相补偿，已证明会把沙尘暴误判为「一般」
    "aggregation": "geometric",
    # 隶属度下限：避免某个因子得 0 后几何平均直接归零，产生分数悬崖
    "floor": 0.02,
    "weights": {
        "cloud_cover": 0.22,
        "cloud_cover_low": 0.15,
        "cloud_cover_mid": 0.10,
        "cloud_cover_high": 0.05,
        "aod550": 0.18,
        "visibility_km": 0.15,
        "ozone_du": 0.15,
    },
    "thresholds": {
        "cloud_cover":      {"dir": "neg",  "good": 15.0, "bad": 80.0},
        "cloud_cover_low":  {"dir": "neg",  "good": 5.0,  "bad": 70.0},
        "cloud_cover_mid":  {"dir": "neg",  "good": 10.0, "bad": 80.0},
        "cloud_cover_high": {"dir": "neg",  "good": 20.0, "bad": 90.0},
        "aod550":           {"dir": "neg",  "good": 0.10, "bad": 0.45},
        "visibility_km":    {"dir": "pos",  "bad": 6.0,   "good": 20.0},
        # 总柱臭氧：偏低→蓝不纯，偏高→偏紫；梯形平坦区为理想
        "ozone_du": {"dir": "trap", "a": 200.0, "b": 300.0, "c": 420.0, "d": 500.0},
    },
}

# 综合质量分：可观赏性因子
QUALITY = {
    # 同 BLUE_INDEX：蓝度差 / 下雨 / 地形全挡，都应直接把整体压下去
    "aggregation": "geometric",
    "floor": 0.02,
    "weights": {
        "blue_index": 0.70,
        "window_minutes": 0.10,
        "terrain_score": 0.08,
        "moon_score": 0.06,
        "precipitation": 0.06,
    },
    "thresholds": {
        "blue_index":     {"dir": "pos", "bad": 0.0,  "good": 100.0},
        "window_minutes": {"dir": "pos", "bad": 8.0,  "good": 25.0},
        "terrain_score":  {"dir": "pos", "bad": 0.0,  "good": 1.0},
        "moon_score":     {"dir": "pos", "bad": 0.0,  "good": 1.0},
        "precipitation":  {"dir": "neg", "good": 0.0, "bad": 0.6},
    },
}

# 分级（从高分到低分依次匹配）
GRADES = [
    {"min": 85, "blue": "很蓝", "quality": "极佳"},
    {"min": 70, "blue": "较蓝", "quality": "好"},
    {"min": 55, "blue": "一般", "quality": "一般偏好"},
    {"min": 40, "blue": "偏灰", "quality": "一般"},
    {"min": 0,  "blue": "很灰", "quality": "差"},
]

# 地形遮挡：蓝天辉光带考虑的仰角上限（度）。
# 蓝调辉光出现在地平线以上约 0~25° 的带状区域，地形地平线越高，
# 被遮掉的辉光越多，评分按 (band_top - H) / band_top 计算。
TERRAIN = {
    "glow_band_top": 25.0,
}

# 置信度：集合预报云量离散度阈值（百分点）
CONFIDENCE = {
    "high_max": 15.0,
    "mid_max": 30.0,
}

# 数据源开关
# 主源设为和风（国内节点，快）；未配置 Key 时会自动回落到 Open-Meteo。
# AOD 与气象解耦：和风不提供 AOD，永远由 Open-Meteo 空气质量接口单独获取。
SOURCES = {
    "primary": "qweather",
    "fallback": "open_meteo",
    # 认证：用 API KEY —— 通过请求头 X-QW-Api-Key 传递（见 sources/qweather.py）。
    # ⚠️ 密钥不写进代码，从环境变量读取（见文件顶部说明）；未设置则走 Open-Meteo。
    "qweather_key": os.environ.get("QWEATHER_KEY", ""),
    "qweather_host": os.environ.get("QWEATHER_HOST", ""),   # 控制台「设置」里的 API Host
    # 小时天气预报 v1（官方主推，1km，全球；旧 /v7/weather/72h 即将弃用）
    # 已实测确认：和风**不提供分层云量**（低/中/高云），只给总云量 cloudCover。
    "qweather_path": "/weather/v1/hourly/{lat}/{lon}",
    "qweather_hours": 72,   # 覆盖「未来 3 天」
}

# 缓存 TTL（秒）；0 表示永久
CACHE = {
    "static_ttl": 0,       # 城市坐标、地形剖面
    "today_ttl": 3600,     # 当日结果 1 小时
    "future_ttl": 21600,   # 未来日期 6 小时
    # AOD 单独缓存。上游 CAMS 全球模式 **12 小时**才更新一次（5 天预报），
    # 取再勤值也不变，故缓存对齐到 12 小时，减少对 Open-Meteo（欧洲节点）的请求。
    "aod_ttl": 43200,
}

# 网络
NETWORK = {
    "timeout_seconds": 8,          # 默认
    "weather_timeout_seconds": 5,  # 主气象
    # AOD：单独超时，超时即剔除该因子。
    # 实测欧洲节点正常延迟约 3.3 s，原设 3 s 会把「正常但稍慢」误杀；
    # 放宽到 5 s，配合前端进度条（广告展示时间正好覆盖这段等待）。
    "aod_timeout_seconds": 5,
    "ensemble_timeout_seconds": 5, # 多模型（置信度）
    "max_workers": 4,              # 并发请求数
}
