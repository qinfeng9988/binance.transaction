# WebSocket K 线数据获取设计

## 背景

当前 K 线数据通过 REST API 轮询获取，存在以下问题：

1. **限流风险**：多币种多周期在同一时刻并发请求，触发 Binance `-1003` (WAY_TOO_MANY_REQUESTS)
2. **连接重置**：5 分钟轮询间隔导致连接空闲超时，频繁出现 "Resetting dropped connection"
3. **价格滞后**：REST 模式下实时价格最多延迟 5 分钟（5m 周期），止损检查不及时
4. **S/R 查询价格不准**：query_server 的 current_price 来自 K 线 cache，可能滞后数分钟

## 目标

- 将 K 线数据获取从 REST 轮询切换为 WebSocket 推送
- WebSocket 断连时自动降级到 REST，后台持续重连 WebSocket
- 重连成功后自动切回 WebSocket
- 提供实时价格缓存，供所有模块使用
- 保持策略层接口不变，零修改迁移
- 支持配置热重载时动态增减订阅

## 方案选择

**方案 B：拆分 Fetcher 类**

新建 `BaseKlineFetcher` → `WebSocketKlineFetcher` + `RESTKlineFetcher`，`KlineDataManager` 同时持有两个 fetcher，运行时按优先级切换。

选择理由：降级切换是核心需求，拆分 Fetcher 让切换逻辑自然落在 KlineDataManager 层，职责清晰，可独立测试。

## 架构

### 文件布局

```
quant_trader/core/
├── kline_manager.py      # 改造：调度层，持有两个 fetcher
├── kline_fetcher.py      # 新建：BaseKlineFetcher + WebSocketKlineFetcher + RESTKlineFetcher
└── ...
```

### 类结构

```
BaseKlineFetcher (ABC)
├─ start(symbol_timeframes) → async
├─ stop() → async
├─ subscribe(new_items) → async          # 动态新增订阅
├─ unsubscribe(removed_items) → async    # 动态移除订阅
├─ on_kline: Callable                   # K 线更新回调
├─ is_connected: bool

WebSocketKlineFetcher(BaseKlineFetcher)
├─ ccxt.pro.binance 实例
├─ watchOHLCVForSymbols 循环
├─ 重连计数器 (max=3)
├─ 降级回调: on_fallback_to_rest
├─ 后台重连 (10min → 30min 间隔)
├─ 代理配置 (aiohttp proxies)
├─ K 线收盘检测 (K 线切换判断)

RESTKlineFetcher(BaseKlineFetcher)
├─ BinanceAdapter + 轮询逻辑
├─ 对齐到 K 线收盘时刻
├─ 从现有 _fetch_loop 迁移
├─ 每次 fetch 后更新实时价格缓存
```

### KlineDataManager 改造

```
KlineDataManager
├─ ws_fetcher: WebSocketKlineFetcher     # 始终存在
├─ rest_fetcher: RESTKlineFetcher        # 降级时激活
├─ _active_source: "ws" | "rest"         # 当前数据来源
├─ _realtime_prices: dict[str, float]    # symbol → 最新价
│
├─ start()
│   ├─ 启动 ws_fetcher
│   └─ ws_fetcher.on_fallback → 激活 rest_fetcher
│
├─ _on_kline_update(symbol, tf, klines, event, source)
│   ├─ 更新 _realtime_prices (每次都更新)
│   ├─ 更新 _klines_cache
│   ├─ if event == "closed": _store_klines()
│   ├─ if source == "ws" and _active_source == "rest":
│   │   ├─ 切 _active_source = "ws"
│   │   └─ rest_fetcher.stop()
│   └─ 通知 _subscribers
│
├─ update_subscriptions(new_set)         # 配置热重载
│   ├─ diff 新增/移除
│   ├─ 通知当前活跃的 fetcher
│   └─ 更新 _subscribers / _klines_cache
│
├─ subscribe(symbol, tf) → Queue         # 接口不变
├─ get_klines(symbol, tf) → list[dict]   # 接口不变
├─ get_realtime_price(symbol) → float|None  # 新增
└─ shutdown()
```

## 核心流程

### WebSocket 重连与降级

```
WebSocketKlineFetcher.run():

reconnect_attempt = 0

while running:
  try:
    exchange = ccxt.pro.binance(config)
    klines = await watchOHLCVForSymbols(subscriptions)
    reconnect_attempt = 0          # 成功则重置
    callback(klines, event)

  except ConnectionError / Timeout:
    reconnect_attempt += 1

    if reconnect_attempt <= 3:
      # 前三次：快速重连（指数退避 2s → 4s → 8s）
      delay = 2 ** reconnect_attempt
      await sleep(delay)

    else:
      # 超过3次：通知降级到 REST
      on_fallback_to_rest()

      # 后台持续重连
      if reconnect_attempt <= 5:
        delay = 600   # 10 分钟
      else:
        delay = 1800  # 30 分钟
      await sleep(delay)

    # 重连成功后 callback 被调用 → KlineDataManager 自动切回 ws
```

### 降级切换时序

```
t=0      WebSocket 正常推送，_active_source = "ws"，rest_fetcher 未启动

t=X      WebSocket 断线，ws_fetcher 快速重连 (2s, 4s, 8s)

t=X+14   3次重连失败
         ws_fetcher: on_fallback_to_rest()
         KlineDataManager:
           ├─ 启动 rest_fetcher.start()
           ├─ _active_source = "rest"
           └─ 策略继续收到 K 线数据（REST 提供）

t=X+14~Y ws_fetcher 后台重连 (10min → 30min 间隔)
         rest_fetcher 持续提供数据

t=Y      WebSocket 重连成功
         ws_fetcher: on_kline(symbol, tf, klines, "ws")
         KlineDataManager:
           ├─ _active_source = "ws"
           ├─ rest_fetcher.stop()
           └─ 策略无缝切回 WebSocket 数据
```

### K 线收盘判断（方案 A：K 线切换检测）

WebSocket 每秒推送"正在形成"的 K 线，策略只在 K 线收盘后执行。

收盘判断：记录上一次推送的 K 线开盘时间，如果新推送的 K 线开盘时间变了，说明上一根 K 线已收盘。

```
WebSocketKlineFetcher 内部:
  _last_kline_open: dict[(symbol, tf), int]  # 上次推送的 K 线开盘时间戳

  每次收到推送:
    current_open = klines[0]["timestamp"]
    last_open = _last_kline_open.get((symbol, tf))

    if last_open is not None and current_open != last_open:
      # K 线切换 → 上一根已收盘
      on_kline(symbol, tf, klines, event="closed")
    else:
      # 正在形成的 K 线更新
      on_kline(symbol, tf, klines, event="update")

    _last_kline_open[(symbol, tf)] = current_open
```

优势：
- 不依赖本地时钟，自然处理网络延迟
- 与 Binance 的 is_closed 语义一致

### 策略层适配

Queue 消息格式从 `list[dict]` 改为 `dict`：

```python
# 旧: klines = await kline_queue.get()
# 新: msg = await kline_queue.get()
#     klines = msg["klines"]
#     event = msg["event"]  # "update" | "closed"

if event == "update":
    # 只更新持仓价格（止损检查）
    current_price = klines[0]["close"]
    self.paper_engine.update_prices(symbol, current_price, self.data_store)
    continue

if event == "closed":
    # 执行策略逻辑（与现有代码一致）
    ...
```

RESTKlineFetcher 只发 `"closed"` 事件（REST 本来就是对齐到收盘后拉取的），策略层无需区分数据来源。

### 实时价格缓存

KlineDataManager 维护 `_realtime_prices: dict[str, float]`：

- **WebSocket 模式**：每次推送（update 和 closed）都更新，价格实时
- **REST 模式**：每次 fetch 后用 `klines[0]["close"]` 更新（klines[0] 是正在形成的 K 线，close 就是最新价），不需要额外 REST 请求

受益模块：
1. `serialize_status` — 替代 `_fetch_realtime_price()` 的 REST 请求，零额外开销
2. `query_server` — S/R 查询的 current_price 改为从缓存获取，不再滞后
3. `paper_trading.update_prices` — WebSocket update 事件每次都更新，止损检查更及时

### Bug 修复：REST 模式实时价格

现有 bug：REST 模式下 query_server 的 current_price 来自 K 线 cache，可能滞后数分钟。

修复：RESTKlineFetcher 每次 fetch 后，将 `klines[0]["close"]` 写入 `_realtime_prices`。query_server 和 serialize_status 统一从 `kline_manager.get_realtime_price(symbol)` 获取价格。

### 动态订阅更新

配置热重载后，Engine 重新计算 symbol_timeframes 集合，调用 `KlineDataManager.update_subscriptions(new_set)`：

```
update_subscriptions(new_set):
  added = new_set - old_set
  removed = old_set - new_set

  if ws_fetcher.is_connected:
    ws_fetcher.subscribe(added)
    ws_fetcher.unsubscribe(removed)

  if rest_fetcher.is_running:
    rest_fetcher.subscribe(added)    # 启动新的 _fetch_loop
    rest_fetcher.unsubscribe(removed) # 取消对应的 _fetch_loop

  清理 _klines_cache 和 _subscribers 中被移除的项
```

ccxt.pro 的 `watchOHLCVForSymbols` 每次返回一个有更新的 (symbol, timeframe) 的 K 线数据（不是批量），每次调用时传入最新订阅列表即可动态增减。

### 数据连续性

- **降级时**：REST 启动后立即拉一次全量（limit=151），覆盖 cache，策略无感知
- **恢复时**：WebSocket 推来的第一条数据直接覆盖 cache，策略无感知
- **不合并、不对比**：谁的数据新就用谁的，简单可靠

## 错误处理

| 场景 | 处理 |
|------|------|
| 连接断开 | 重连机制（3次快速 → 后台慢速）+ 降级 REST |
| 订阅失败 (symbol 不存在) | 记录 warning，跳过该订阅，不影响其他 |
| 消息格式异常 | 记录 warning，跳过该条消息，不重连 |
| 代理连接失败 | 与连接断开相同，走重连+降级流程 |
| Binance 服务器维护 | 连接断开，走重连流程，后台重连自动恢复 |
| REST 降级也失败 | 记录 error，策略暂停等待数据 |

## 日志规范

| 级别 | 事件 |
|------|------|
| INFO | WebSocket 连接成功、降级到 REST、WebSocket 恢复切回、订阅变更 |
| WARNING | WebSocket 断线开始重连 (attempt N)、消息解析异常、订阅失败 |
| ERROR | 3次重连失败降级到 REST、REST 降级也失败 |

## 技术选型

- **WebSocket 库**：ccxt.pro（已安装，支持 watchOHLCV / watchOHLCVForSymbols）
- **代理**：保持现有代理配置（ccxt.pro 底层 aiohttp，代理配置方式与 REST 一致）
- **REST 降级**：复用现有 BinanceAdapter

## 不变项

- 策略层接口：`subscribe()` / `get_klines()` 保持不变
- 数据格式：K 线数据仍为 `list[dict]`，字段 timestamp/open/high/low/close/volume
- 数据库存储：`_store_klines()` 逻辑不变
- 风控/下单：不受影响
