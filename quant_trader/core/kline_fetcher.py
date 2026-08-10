"""K 线数据获取层 - 提供 REST 和 WebSocket 两种实现。

设计目标：
1. 统一接口（BaseKlineFetcher），KlineDataManager 可透明切换
2. WebSocket 优先，REST 降级
3. 支持动态增减订阅（配置热重载）
"""
