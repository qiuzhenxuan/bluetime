"""bluetime —— 蓝调时刻评分引擎

纯标准库实现（无第三方依赖），便于整体搬进微信云函数（256MB 内存、冷启动敏感）。
模块划分：
    solar       太阳几何：蓝调窗口、日出日落、太阳高度/方位（离线）
    moon        月相与月面照明比例（离线）
    horizon     地形地平线遮挡（PVGIS API，可回退）
    sources     气象与大气成分取数（Open-Meteo 为主，和风天气为辅）
    score       双指标评分：蓝度指数 + 综合质量分
    confidence  置信度（集合预报离散度）
    cache       缓存层（TTL + 防并发击穿）
    report      输出格式化（文本 / JSON）
    cli         命令行入口（本地调试验证用）
"""

__version__ = "0.1.0"
