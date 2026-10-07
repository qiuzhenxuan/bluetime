"""数据源层：气象与大气成分取数。

对外只暴露一个标准化结构，屏蔽 和风天气 / Open-Meteo 的字段差异。
气象走主备切换（和风为主），AOD 固定走 Open-Meteo 并与气象并发获取。
"""

from .base import WeatherBundle, fetch_all  # noqa: F401
