# WebSocket K 线数据获取 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 K 线数据获取从 REST 轮询切换为 WebSocket 推送，断连时降级到 REST，提供实时价格缓存，并修复 REST 模式下实时价格滞后的 bug。

**Architecture:** 新建 `kline_fetcher.py` 包含 `BaseKlineFetcher` 抽象基类和 `WebSocketKlineFetcher` / `RESTKlineFetcher` 两个实现。`KlineDataManager` 同时持有两个 fetcher，WebSocket 优先，断连降级 REST，重连后切回。Fetcher 通过回调通知 KlineDataManager 数据更新（区分 `update` 和 `closed` 事件）。策略层订阅接口保持不变，但 Queue 消息从 `list[dict]` 改为 `dict` 以携带事件类型。

**Tech Stack:** ccxt.pro（已安装）、aiohttp（ccxt.pro 底层）、asyncio、pytest

## Global Constraints

- Python 3.11+（项目使用 `str | None` 语法）
- ccxt 4.5.56 已安装，支持 `ccxt.pro.binance.watch_ohlcv_for_symbols`
- 代理配置保持现有：`http://127.0.0.1:7890`
- K 线数据格式：`list[dict]`，字段 `timestamp/open/high/low/close/volume`，最新在前（索引 0）
- 数据库存储逻辑不变：`_store_klines()` 只在 K 线收盘时调用
- 策略层接口签名不变：`subscribe(symbol, tf) → Queue`，`get_klines(symbol, tf) → list[dict]`
- 测试框架：pytest
- 代码风格：遵循现有文件（类型注解、中文 docstring、4 空格缩进）

## 关键技术发现（影响实现）

1. **ccxt.pro 不自动预加载历史**：`watch_ohlcv_for_symbols` 订阅后只推送新更新，内部 `ArrayCacheByTimestamp` 从空开始。WebSocket 首次连接后需要 REST 拉取 151 根历史 K 线作为初始缓存。

2. **`watch_ohlcv_for_symbols` 返回格式**：`{symbol: {timeframe: [[ts, o, h, l, c, v], ...]}}`，内层是数组列表（不是 dict）。需要转换为现有 dict 格式。

3. **每条消息只含 1 根 K 线**：Binance WebSocket kline stream 每次推送当前正在形成的 K 线或刚收盘的 K 线。需要把单根 K 线合并到缓存（替换最后一根或追加新根）。

4. **收盘判断**：记录上次推送的开盘时间戳，新推送开盘时间变了 → 上一根已收盘。

5. **代理配置**：ccxt.pro 通过 aiohttp 实现，代理设置用 `aiohttp_proxy` 或在 session 创建时配置。需要使用 `wsProxy`/`wssProxy` 选项或 `aiohttp_trust_env`。

---

## File Structure

```
quant_trader/core/
├── kline_fetcher.py      # 新建：BaseKlineFetcher + WebSocketKlineFetcher + RESTKlineFetcher
├── kline_manager.py      # 改造：调度层，持有两个 fetcher
├── serialization.py      # 改造：用 get_realtime_price 替代 REST 实时价格请求
├── query_server.py       # 改造：用 get_realtime_price 替代 K 线 cache 价格
├── binance_adapter.py    # 不变（RESTKlineFetcher 复用）
└── ...

quant_trader/
└── main.py               # 改造：策略层 Queue 消息格式适配 + 配置热重载订阅更新

tests/
├── test_kline_fetcher.py  # 新建：Fetcher 单元测试
└── test_kline_manager.py  # 新建：KlineDataManager 集成测试
```

---

## Task 1: 创建分支与脚手架文件

**Files:**
- Create: `quant_trader/core/kline_fetcher.py`（空文件，仅含模块 docstring）
- Test: 无（本任务只创建脚手架）

**Interfaces:**
- Produces: 文件 `quant_trader/core/kline_fetcher.py` 存在，后续任务在此填充

- [ ] **Step 1: 创建分支**

```bash
git checkout develop
git checkout -b feature/websocket-kline-fetcher
```

验证：`git branch --show-current` 输出 `feature/websocket-kline-fetcher`

- [ ] **Step 2: 创建脚手架文件**

写入 `quant_trader/core/kline_fetcher.py`：

```python
"""K 线数据获取层 - 提供 REST 和 WebSocket 两种实现。

设计目标：
1. 统一接口（BaseKlineFetcher），KlineDataManager 可透明切换
2. WebSocket 优先，REST 降级
3. 支持动态增减订阅（配置热重载）
"""
```

- [ ] **Step 3: 提交脚手架**

```bash
git add quant_trader/core/kline_fetcher.py
git commit -m "chore: add kline_fetcher module scaffold

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: BaseKlineFetcher 抽象基类

**Files:**
- Modify: `quant_trader/core/kline_fetcher.py`
- Test: `tests/test_kline_fetcher.py`

**Interfaces:**
- Produces: `BaseKlineFetcher` 类，签名如下：
  - `__init__(self, on_kline: Callable[[str, str, list[dict], str], None])`
  - `async start(self, symbol_timeframes: set[tuple[str, str]]) -> None`
  - `async stop(self) -> None`
  - `async subscribe(self, new_items: set[tuple[str, str]]) -> None`
  - `async unsubscribe(self, removed_items: set[tuple[str, str]]) -> None`
  - `is_running: bool`（属性）
  - 回调签名：`on_kline(symbol: str, timeframe: str, klines: list[dict], event: str) -> None`，`event` 取值 `"update"` 或 `"closed"`

- [ ] **Step 1: 写失败测试**

写入 `tests/test_kline_fetcher.py`：

```python
"""kline_fetcher 模块单元测试。"""

import pytest

from quant_trader.core.kline_fetcher import BaseKlineFetcher


def test_base_fetcher_is_abstract():
    """BaseKlineFetcher 是抽象基类，不能直接实例化。"""
    with pytest.raises(TypeError):
        BaseKlineFetcher(on_kline=lambda *_: None)


def test_base_fetcher_subclass_must_implement_abstract_methods():
    """子类必须实现所有抽象方法才能实例化。"""

    class IncompleteFetcher(BaseKlineFetcher):
        pass

    with pytest.raises(TypeError):
        IncompleteFetcher(on_kline=lambda *_: None)


def test_base_fetcher_concrete_subclass_can_instantiate():
    """完整实现的子类可以实例化。"""

    class StubFetcher(BaseKlineFetcher):
        async def start(self, symbol_timeframes):
            pass

        async def stop(self):
            pass

        async def subscribe(self, new_items):
            pass

        async def unsubscribe(self, removed_items):
            pass

        @property
        def is_running(self):
            return False

    fetcher = StubFetcher(on_kline=lambda *_: None)
    assert fetcher.is_running is False
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/qinj057/develop/binance.transaction-ui && python3 -m pytest tests/test_kline_fetcher.py -v`

Expected: FAIL，`ImportError: cannot import name 'BaseKlineFetcher'`

- [ ] **Step 3: 实现 BaseKlineFetcher**

替换 `quant_trader/core/kline_fetcher.py` 内容：

```python
"""K 线数据获取层 - 提供 REST 和 WebSocket 两种实现。

设计目标：
1. 统一接口（BaseKlineFetcher），KlineDataManager 可透明切换
2. WebSocket 优先，REST 降级
3. 支持动态增减订阅（配置热重载）
"""

from abc import ABC, abstractmethod
from collections.abc import Callable


class BaseKlineFetcher(ABC):
    """K 线获取器抽象基类。

    子类需实现 start/stop/subscribe/unsubscribe 和 is_running 属性。
    收到 K 线数据时通过 on_kline 回调通知上层。

    回调签名:
        on_kline(symbol: str, timeframe: str, klines: list[dict], event: str)
        event 取值:
            "update" - K 线正在形成（WebSocket 实时推送）
            "closed" - K 线已收盘（策略执行时机）
    """

    def __init__(self, on_kline: Callable[[str, str, list[dict], str], None]):
        self._on_kline = on_kline
        self._symbol_timeframes: set[tuple[str, str]] = set()

    @abstractmethod
    async def start(self, symbol_timeframes: set[tuple[str, str]]) -> None:
        """启动 K 线获取循环。"""

    @abstractmethod
    async def stop(self) -> None:
        """停止获取，释放资源。"""

    @abstractmethod
    async def subscribe(self, new_items: set[tuple[str, str]]) -> None:
        """动态新增订阅。"""

    @abstractmethod
    async def unsubscribe(self, removed_items: set[tuple[str, str]]) -> None:
        """动态移除订阅。"""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """是否正在运行（连接活跃）。"""

    def _notify(self, symbol: str, timeframe: str, klines: list[dict], event: str) -> None:
        """触发 K 线更新回调。"""
        if self._on_kline is not None:
            self._on_kline(symbol, timeframe, klines, event)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/qinj057/develop/binance.transaction-ui && python3 -m pytest tests/test_kline_fetcher.py -v`

Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add quant_trader/core/kline_fetcher.py tests/test_kline_fetcher.py
git commit -m "feat: add BaseKlineFetcher abstract base class

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: K 线合并与收盘判断工具函数

**Files:**
- Modify: `quant_trader/core/kline_fetcher.py`（追加模块级函数）
- Test: `tests/test_kline_fetcher.py`（追加测试）

**Interfaces:**
- Produces:
  - `merge_kline_update(cached: list[dict], new_kline: dict) -> tuple[list[dict], str]`
    - 返回 `(merged_list, event)`，`event` 为 `"update"`（同一根 K 线更新）或 `"closed"`（新 K 线出现，意味着上一根已收盘）
    - 合并逻辑：若 `new_kline["timestamp"]` 等于 `cached[0]["timestamp"]`，替换 cached[0]，返回 `("update")`；若 `new_kline["timestamp"]` 大于 `cached[0]["timestamp"]`，插入到 cached[0] 前，返回 `("closed")`
    - 缓存为空时直接返回 `[new_kline]`，event 为 `"update"`
    - 限制最大长度 200（防止异常数据撑爆缓存）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_kline_fetcher.py`：

```python
from quant_trader.core.kline_fetcher import merge_kline_update


def _kline(ts, close):
    """构造测试用 K 线 dict。"""
    return {"timestamp": ts, "open": 100, "high": 101, "low": 99, "close": close, "volume": 1.0}


def test_merge_kline_update_empty_cache():
    """缓存为空时，新 K 线直接作为初始数据，事件为 update。"""
    result, event = merge_kline_update([], _kline(1000, 50.0))
    assert result == [_kline(1000, 50.0)]
    assert event == "update"


def test_merge_kline_update_same_timestamp():
    """新 K 线时间戳与缓存首根相同 → 替换，事件为 update。"""
    cached = [_kline(1000, 50.0), _kline(900, 49.0)]
    result, event = merge_kline_update(cached, _kline(1000, 51.0))
    assert result[0]["close"] == 51.0
    assert result[1] == _kline(900, 49.0)
    assert len(result) == 2
    assert event == "update"


def test_merge_kline_update_new_timestamp_means_close():
    """新 K 线时间戳大于缓存首根 → 插入到最前，事件为 closed。"""
    cached = [_kline(1000, 50.0), _kline(900, 49.0)]
    result, event = merge_kline_update(cached, _kline(2000, 51.0))
    assert result[0]["timestamp"] == 2000
    assert result[1]["timestamp"] == 1000
    assert result[2]["timestamp"] == 900
    assert len(result) == 3
    assert event == "closed"


def test_merge_kline_update_truncates_to_max_length():
    """缓存超过 200 根时自动截断尾部。"""
    cached = [_kline(ts, float(ts)) for ts in range(200, 0, -1)]  # 200 根
    new_kline = _kline(1000, 999.0)
    result, event = merge_kline_update(cached, new_kline)
    assert len(result) == 200
    assert result[0]["timestamp"] == 1000
    assert event == "closed"


def test_merge_kline_update_outdated_timestamp_ignored():
    """新 K 线时间戳小于缓存首根 → 忽略（防止历史数据污染）。"""
    cached = [_kline(1000, 50.0), _kline(900, 49.0)]
    result, event = merge_kline_update(cached, _kline(800, 48.0))
    assert result == cached  # 缓存不变
    assert event == "update"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/qinj057/develop/binance.transaction-ui && python3 -m pytest tests/test_kline_fetcher.py -v -k merge_kline`

Expected: FAIL，`ImportError: cannot import name 'merge_kline_update'`

- [ ] **Step 3: 实现工具函数**

在 `quant_trader/core/kline_fetcher.py` 末尾追加：

```python
_MAX_KLINE_CACHE = 200


def merge_kline_update(cached: list[dict], new_kline: dict) -> tuple[list[dict], str]:
    """把单根新 K 线合并到缓存。

    返回 (merged_list, event):
        event="update": 新 K 线与缓存首根是同一根（时间戳相同），仅更新
        event="closed": 新 K 线时间戳更大，意味着上一根已收盘，新根开始

    缓存按时间戳降序排列（最新在前）。最大长度 _MAX_KLINE_CACHE，超出则截断尾部。
    """
    if not cached:
        return [new_kline], "update"

    new_ts = new_kline["timestamp"]
    head_ts = cached[0]["timestamp"]

    if new_ts == head_ts:
        merged = [new_kline] + cached[1:]
        return merged, "update"

    if new_ts > head_ts:
        merged = [new_kline] + cached
        if len(merged) > _MAX_KLINE_CACHE:
            merged = merged[:_MAX_KLINE_CACHE]
        return merged, "closed"

    # new_ts < head_ts: 历史/乱序数据，忽略
    return cached, "update"
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/qinj057/develop/binance.transaction-ui && python3 -m pytest tests/test_kline_fetcher.py -v -k merge_kline`

Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add quant_trader/core/kline_fetcher.py tests/test_kline_fetcher.py
git commit -m "feat: add kline merge utility for websocket updates

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: RESTKlineFetcher 实现

**Files:**
- Modify: `quant_trader/core/kline_fetcher.py`
- Test: `tests/test_kline_fetcher.py`

**Interfaces:**
- Consumes: `BinanceAdapter`（来自 `quant_trader.exchange.binance_adapter`）
- Consumes: `TIMEFRAME_SECONDS` 常量（来自 `quant_trader.main`，通过函数参数注入避免循环导入）
- Produces: `RESTKlineFetcher` 类，构造函数 `__init__(self, exchange: BinanceAdapter, on_kline: Callable, timeframes_seconds: dict[str, int], buffer_seconds: int = 1)`
  - `start(symbol_timeframes)` 启动每个 (symbol, tf) 的 `_fetch_loop`
  - `stop()` 取消所有任务
  - `subscribe(new_items)` 启动新增 (symbol, tf) 的循环
  - `unsubscribe(removed_items)` 取消对应循环
  - `is_running` 始终返回 True（只要 start 被调用过）
  - 每次 fetch 后通过 `on_kline` 回调，event 固定为 `"closed"`（REST 模式天然在收盘后拉取）
  - 每次 fetch 后更新实时价格缓存（通过 `on_kline` 回调中的 klines[0]["close"] 实现，KlineDataManager 处理）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_kline_fetcher.py`：

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock

from quant_trader.core.kline_fetcher import RESTKlineFetcher


def _make_mock_exchange():
    """构造一个模拟 BinanceAdapter。"""
    exchange = MagicMock()
    exchange.fetch_ohlcv = AsyncMock(return_value=[
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1.0},
    ])
    return exchange


def test_rest_fetcher_initial_state():
    """RESTKlineFetcher 在 start 前 is_running 为 False。"""
    exchange = _make_mock_exchange()
    fetcher = RESTKlineFetcher(
        exchange=exchange,
        on_kline=lambda *_: None,
        timeframes_seconds={"5m": 300},
    )
    assert fetcher.is_running is False


def test_rest_fetcher_starts_and_stops():
    """start 后 is_running 为 True，stop 后为 False。"""
    exchange = _make_mock_exchange()
    fetcher = RESTKlineFetcher(
        exchange=exchange,
        on_kline=lambda *_: None,
        timeframes_seconds={"5m": 300},
        buffer_seconds=0,
    )

    asyncio.run(fetcher.start({("BTC/USDT", "5m")}))
    assert fetcher.is_running is True
    assert ("BTC/USDT", "5m") in fetcher._symbol_timeframes

    asyncio.run(fetcher.stop())
    assert fetcher.is_running is False


def test_rest_fetcher_subscribe_adds_new_subscription():
    """subscribe 启动新增 (symbol, tf) 的循环。"""
    exchange = _make_mock_exchange()
    fetcher = RESTKlineFetcher(
        exchange=exchange,
        on_kline=lambda *_: None,
        timeframes_seconds={"5m": 300, "1h": 3600},
        buffer_seconds=0,
    )

    asyncio.run(fetcher.start({("BTC/USDT", "5m")}))
    asyncio.run(fetcher.subscribe({("ETH/USDT", "5m")}))

    assert ("BTC/USDT", "5m") in fetcher._symbol_timeframes
    assert ("ETH/USDT", "5m") in fetcher._symbol_timeframes

    asyncio.run(fetcher.stop())


def test_rest_fetcher_unsubscribe_removes_subscription():
    """unsubscribe 取消对应循环。"""
    exchange = _make_mock_exchange()
    fetcher = RESTKlineFetcher(
        exchange=exchange,
        on_kline=lambda *_: None,
        timeframes_seconds={"5m": 300},
        buffer_seconds=0,
    )

    asyncio.run(fetcher.start({("BTC/USDT", "5m"), ("ETH/USDT", "5m")}))
    asyncio.run(fetcher.unsubscribe({("ETH/USDT", "5m")}))

    assert ("BTC/USDT", "5m") in fetcher._symbol_timeframes
    assert ("ETH/USDT", "5m") not in fetcher._symbol_timeframes

    asyncio.run(fetcher.stop())
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/qinj057/develop/binance.transaction-ui && python3 -m pytest tests/test_kline_fetcher.py -v -k rest_fetcher`

Expected: FAIL，`ImportError: cannot import name 'RESTKlineFetcher'`

- [ ] **Step 3: 实现 RESTKlineFetcher**

在 `quant_trader/core/kline_fetcher.py` 顶部 import 区追加：

```python
import asyncio
import logging
import time as _time
```

在文件末尾追加：

```python
logger = logging.getLogger(__name__)


class RESTKlineFetcher(BaseKlineFetcher):
    """REST 轮询式 K 线获取器。

    从现有 KlineDataManager._fetch_loop 迁移而来。
    每次 fetch 后通过 on_kline 回调通知，event 固定为 "closed"。
    """

    def __init__(
        self,
        exchange,
        on_kline: Callable[[str, str, list[dict], str], None],
        timeframes_seconds: dict[str, int],
        buffer_seconds: int = 1,
    ):
        super().__init__(on_kline)
        self.exchange = exchange
        self.timeframes_seconds = timeframes_seconds
        self.buffer_seconds = buffer_seconds
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._running = False

    async def start(self, symbol_timeframes: set[tuple[str, str]]) -> None:
        """启动所有 (symbol, tf) 的轮询循环。"""
        self._running = True
        self._symbol_timeframes = set(symbol_timeframes)
        for symbol, timeframe in symbol_timeframes:
            self._start_one(symbol, timeframe)
        logger.info("RESTKlineFetcher 已启动: %d 个订阅", len(self._tasks))

    def _start_one(self, symbol: str, timeframe: str) -> None:
        """启动单个 (symbol, tf) 的循环。"""
        key = (symbol, timeframe)
        if key in self._tasks and not self._tasks[key].done():
            return  # 已在运行
        task = asyncio.create_task(self._fetch_loop(symbol, timeframe))
        self._tasks[key] = task

    async def _fetch_loop(self, symbol: str, timeframe: str) -> None:
        """单个 (symbol, tf) 的轮询循环。对齐到 K 线收盘时刻。"""
        interval = self.timeframes_seconds.get(timeframe, 300)
        logger.info("REST 拉取已启动: %s %s (间隔=%d秒)", symbol, timeframe, interval)

        while self._running:
            try:
                klines = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=151)
                if klines:
                    # ccxt 返回最旧在前，策略需要最新在前
                    klines.reverse()
                    self._notify(symbol, timeframe, klines, "closed")
            except Exception:
                logger.exception("REST K 线拉取异常: %s %s", symbol, timeframe)

            # 对齐到下一根 K 线收盘后 buffer_seconds 秒
            now = _time.time()
            next_execution = ((int(now) // interval) + 1) * interval + self.buffer_seconds
            sleep_time = max(1, next_execution - now)
            await asyncio.sleep(sleep_time)

    async def stop(self) -> None:
        """停止所有轮询循环。"""
        self._running = False
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        self._symbol_timeframes.clear()
        logger.info("RESTKlineFetcher 已停止")

    async def subscribe(self, new_items: set[tuple[str, str]]) -> None:
        """动态新增订阅。"""
        for symbol, timeframe in new_items:
            self._symbol_timeframes.add((symbol, timeframe))
            self._start_one(symbol, timeframe)
        logger.info("RESTKlineFetcher 新增订阅: %d", len(new_items))

    async def unsubscribe(self, removed_items: set[tuple[str, str]]) -> None:
        """动态移除订阅。"""
        for key in removed_items:
            task = self._tasks.pop(key, None)
            if task and not task.done():
                task.cancel()
            self._symbol_timeframes.discard(key)
        logger.info("RESTKlineFetcher 移除订阅: %d", len(removed_items))

    @property
    def is_running(self) -> bool:
        return self._running
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/qinj057/develop/binance.transaction-ui && python3 -m pytest tests/test_kline_fetcher.py -v -k rest_fetcher`

Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add quant_trader/core/kline_fetcher.py tests/test_kline_fetcher.py
git commit -m "feat: implement RESTKlineFetcher

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: WebSocketKlineFetcher 实现

**Files:**
- Modify: `quant_trader/core/kline_fetcher.py`
- Test: `tests/test_kline_fetcher.py`

**Interfaces:**
- Consumes: `BinanceAdapter`（用于 bootstrap 历史数据和代理配置）
- Produces: `WebSocketKlineFetcher` 类，构造函数：
  ```
  __init__(
      self,
      rest_exchange,            # BinanceAdapter，用于 bootstrap 历史和读取代理配置
      on_kline: Callable,
      on_fallback_to_rest: Callable[[], None],  # 3次重连失败时触发降级
      timeframes_seconds: dict[str, int],
      proxy: str | None = None,
      max_fast_reconnect: int = 3,
      bg_retry_short_delay: int = 600,  # 10 分钟
      bg_retry_long_delay: int = 1800,  # 30 分钟
      bg_retry_short_count: int = 2,    # 前2次走 short_delay
  )
  ```
  - `start(symbol_timeframes)`:
      1. 为每个 (symbol, tf) 调 REST bootstrap 拉 151 根历史 K 线，立即 on_kline(event="closed") 填充缓存
      2. 启动主循环 `_ws_loop`
  - `_ws_loop`:
      - 创建 ccxt.pro.binance 实例（带代理）
      - 循环调用 `watch_ohlcv_for_symbols`
      - 收到消息后 merge_kline_update → on_kline
      - 异常 → 重连流程（前3次快速 → 之后后台慢速 + 触发降级）
  - `is_running`:
      - True 表示 WebSocket 连接活跃且正在推送数据
      - False 表示断连/降级中
  - `stop()`: 设置停止标志，关闭 ccxt.pro 实例
  - `subscribe/unsubscribe`: 更新 `_symbol_timeframes`，下一轮 watch 自动包含新列表

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_kline_fetcher.py`：

```python
from quant_trader.core.kline_fetcher import WebSocketKlineFetcher


def test_ws_fetcher_initial_state():
    """WebSocketKlineFetcher 在 start 前 is_running 为 False。"""
    exchange = _make_mock_exchange()
    fetcher = WebSocketKlineFetcher(
        rest_exchange=exchange,
        on_kline=lambda *_: None,
        on_fallback_to_rest=lambda: None,
        timeframes_seconds={"5m": 300},
        proxy=None,
    )
    assert fetcher.is_running is False


def test_ws_fetcher_bootstrap_calls_rest_and_notifies():
    """start 时先通过 REST 拉取历史 K 线填充缓存。"""
    exchange = _make_mock_exchange()
    received = []

    fetcher = WebSocketKlineFetcher(
        rest_exchange=exchange,
        on_kline=lambda sym, tf, klines, event: received.append((sym, tf, event)),
        on_fallback_to_rest=lambda: None,
        timeframes_seconds={"5m": 300},
        proxy=None,
    )

    # 直接测试 bootstrap 方法，不启动 ws_loop
    asyncio.run(fetcher._bootstrap({("BTC/USDT", "5m")}))

    exchange.fetch_ohlcv.assert_called_once_with("BTC/USDT", "5m", limit=151)
    assert received == [("BTC/USDT", "5m", "closed")]


def test_ws_fetcher_subscribe_updates_symbol_set():
    """subscribe 添加新 (symbol, tf) 到订阅集合。"""
    exchange = _make_mock_exchange()
    fetcher = WebSocketKlineFetcher(
        rest_exchange=exchange,
        on_kline=lambda *_: None,
        on_fallback_to_rest=lambda: None,
        timeframes_seconds={"5m": 300, "1h": 3600},
        proxy=None,
    )

    asyncio.run(fetcher.subscribe({("ETH/USDT", "5m")}))
    assert ("ETH/USDT", "5m") in fetcher._symbol_timeframes


def test_ws_fetcher_unsubscribe_removes_from_symbol_set():
    """unsubscribe 从订阅集合移除。"""
    exchange = _make_mock_exchange()
    fetcher = WebSocketKlineFetcher(
        rest_exchange=exchange,
        on_kline=lambda *_: None,
        on_fallback_to_rest=lambda: None,
        timeframes_seconds={"5m": 300},
        proxy=None,
    )
    fetcher._symbol_timeframes = {("BTC/USDT", "5m"), ("ETH/USDT", "5m")}

    asyncio.run(fetcher.unsubscribe({("ETH/USDT", "5m")}))
    assert ("ETH/USDT", "5m") not in fetcher._symbol_timeframes
    assert ("BTC/USDT", "5m") in fetcher._symbol_timeframes


def test_ws_fetcher_merge_incoming_message():
    """收到 WebSocket 推送后，正确合并到缓存并触发回调。"""
    exchange = _make_mock_exchange()
    received_events = []

    fetcher = WebSocketKlineFetcher(
        rest_exchange=exchange,
        on_kline=lambda sym, tf, klines, event: received_events.append((event, klines[0]["close"])),
        on_fallback_to_rest=lambda: None,
        timeframes_seconds={"5m": 300},
        proxy=None,
    )

    # 模拟已有缓存
    fetcher._kline_caches[("BTC/USDT", "5m")] = [
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.0, "volume": 1.0},
    ]

    # 同时间戳更新
    fetcher._handle_ws_message(
        "BTC/USDT", "5m",
        [[1000, 100, 101, 99, 100.5, 1.0]],
    )
    assert received_events[-1] == ("update", 100.5)

    # 新时间戳 → closed
    fetcher._handle_ws_message(
        "BTC/USDT", "5m",
        [[2000, 100, 101, 99, 101.0, 1.0]],
    )
    assert received_events[-1] == ("closed", 101.0)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/qinj057/develop/binance.transaction-ui && python3 -m pytest tests/test_kline_fetcher.py -v -k ws_fetcher`

Expected: FAIL，`ImportError: cannot import name 'WebSocketKlineFetcher'`

- [ ] **Step 3: 实现 WebSocketKlineFetcher**

在 `quant_trader/core/kline_fetcher.py` 顶部 import 区追加：

```python
import ccxt.pro
```

在文件末尾追加：

```python
class WebSocketKlineFetcher(BaseKlineFetcher):
    """WebSocket 推送式 K 线获取器。

    使用 ccxt.pro.binance 的 watch_ohlcv_for_symbols 订阅 K 线推送。
    断连时前 3 次快速重连（指数退避），超过 3 次触发降级回调，
    之后在后台以 10min/30min 间隔持续重连，重连成功后自动恢复。
    """

    def __init__(
        self,
        rest_exchange,
        on_kline: Callable[[str, str, list[dict], str], None],
        on_fallback_to_rest: Callable[[], None],
        timeframes_seconds: dict[str, int],
        proxy: str | None = None,
        max_fast_reconnect: int = 3,
        bg_retry_short_delay: int = 600,
        bg_retry_long_delay: int = 1800,
        bg_retry_short_count: int = 2,
    ):
        super().__init__(on_kline)
        self.rest_exchange = rest_exchange
        self.on_fallback_to_rest = on_fallback_to_rest
        self.timeframes_seconds = timeframes_seconds
        self.proxy = proxy
        self.max_fast_reconnect = max_fast_reconnect
        self.bg_retry_short_delay = bg_retry_short_delay
        self.bg_retry_long_delay = bg_retry_long_delay
        self.bg_retry_short_count = bg_retry_short_count

        self._ws: ccxt.pro.binance | None = None
        self._ws_task: asyncio.Task | None = None
        self._running = False
        self._connected = False  # WebSocket 连接是否活跃
        self._reconnect_attempt = 0
        self._fallback_triggered = False  # 是否已触发降级
        # (symbol, tf) → list[dict] 本地缓存（WebSocket 推送合并维护）
        self._kline_caches: dict[tuple[str, str], list[dict]] = {}

    async def start(self, symbol_timeframes: set[tuple[str, str]]) -> None:
        """启动 WebSocket 获取：先 REST bootstrap，再启动 ws 循环。"""
        self._running = True
        self._symbol_timeframes = set(symbol_timeframes)
        await self._bootstrap(symbol_timeframes)
        self._ws_task = asyncio.create_task(self._ws_loop())

    async def _bootstrap(self, symbol_timeframes: set[tuple[str, str]]) -> None:
        """通过 REST 拉取每个 (symbol, tf) 的初始 151 根 K 线。"""
        for symbol, timeframe in symbol_timeframes:
            try:
                klines = await self.rest_exchange.fetch_ohlcv(symbol, timeframe, limit=151)
                if klines:
                    klines.reverse()  # 最新在前
                    self._kline_caches[(symbol, timeframe)] = klines
                    # 转 dict 格式后通知（rest_exchange 已返回 dict 格式，无需转换）
                    self._notify(symbol, timeframe, klines, "closed")
                    logger.info("WebSocket bootstrap 完成: %s %s (%d 根)", symbol, timeframe, len(klines))
            except Exception:
                logger.exception("WebSocket bootstrap 失败: %s %s", symbol, timeframe)

    async def _ws_loop(self) -> None:
        """主循环：连接 → 订阅 → watch，异常时重连。"""
        while self._running:
            try:
                await self._connect()
                await self._watch()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("WebSocket 异常: %s", e)
                await self._handle_disconnect()

    async def _connect(self) -> None:
        """创建 ccxt.pro.binance 实例并配置代理。"""
        # 每次重连创建新实例，避免状态污染
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

        config: dict = {
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        }
        if self.proxy:
            # ccxt.pro 使用 aiohttp，通过 wssProxy 配置代理
            config["options"]["wsProxy"] = self.proxy
            config["options"]["wssProxy"] = self.proxy

        self._ws = ccxt.pro.binance(config)
        await self._ws.load_markets()

    async def _watch(self) -> None:
        """订阅并监听 K 线推送。"""
        subscriptions = [list(st) for st in self._symbol_timeframes]
        if not subscriptions:
            logger.warning("WebSocket 无订阅，等待 5 秒...")
            await asyncio.sleep(5)
            return

        logger.info("WebSocket 订阅: %s", subscriptions)
        while self._running and self._symbol_timeframes == set(tuple(s) for s in subscriptions):
            result = await self._ws.watch_ohlcv_for_symbols(subscriptions)
            # result 格式: {symbol: {timeframe: [[ts, o, h, l, c, v], ...]}}
            for symbol, tf_dict in result.items():
                for timeframe, candles in tf_dict.items():
                    self._handle_ws_message(symbol, timeframe, candles)

        # 订阅集合发生变化 → 退出内层循环，重新订阅
        if self._running:
            logger.info("订阅集合变化，重新订阅 WebSocket")

    def _handle_ws_message(self, symbol: str, timeframe: str, candles: list[list]) -> None:
        """处理 WebSocket 推送：把单根 K 线合并到缓存。"""
        if not candles:
            return

        # 取最新一根（candles[-1] 是最新的）
        raw = candles[-1]
        new_kline = {
            "timestamp": raw[0],
            "open": raw[1],
            "high": raw[2],
            "low": raw[3],
            "close": raw[4],
            "volume": raw[5],
        }

        key = (symbol, timeframe)
        cached = self._kline_caches.get(key, [])
        merged, event = merge_kline_update(cached, new_kline)
        self._kline_caches[key] = merged

        # 连接活跃标记
        if not self._connected:
            self._connected = True
            self._reconnect_attempt = 0
            self._fallback_triggered = False
            logger.info("WebSocket 连接已恢复: %s %s", symbol, timeframe)

        self._notify(symbol, timeframe, merged, event)

    async def _handle_disconnect(self) -> None:
        """处理断连：前3次快速重连，之后触发降级并后台慢速重连。"""
        self._connected = False
        self._reconnect_attempt += 1

        if self._reconnect_attempt <= self.max_fast_reconnect:
            delay = 2 ** self._reconnect_attempt  # 2s, 4s, 8s
            logger.warning(
                "WebSocket 断线，快速重连 (第%d次, 延迟%d秒)",
                self._reconnect_attempt, delay,
            )
            await asyncio.sleep(delay)
            return

        # 超过 3 次 → 触发降级（只触发一次）
        if not self._fallback_triggered:
            self._fallback_triggered = True
            logger.error("WebSocket 3次重连失败，降级到 REST")
            try:
                self.on_fallback_to_rest()
            except Exception:
                logger.exception("降级回调异常")

        # 后台慢速重连
        if self._reconnect_attempt <= self.max_fast_reconnect + self.bg_retry_short_count:
            delay = self.bg_retry_short_delay  # 10 分钟
        else:
            delay = self.bg_retry_long_delay  # 30 分钟
        logger.warning("WebSocket 后台重连 (第%d次, 延迟%d秒)", self._reconnect_attempt, delay)
        await asyncio.sleep(delay)

    async def stop(self) -> None:
        """停止 WebSocket 获取。"""
        self._running = False
        self._connected = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("WebSocketKlineFetcher 已停止")

    async def subscribe(self, new_items: set[tuple[str, str]]) -> None:
        """动态新增订阅。下一轮 watch 会自动包含新订阅。"""
        # 先 bootstrap 新订阅的历史数据
        await self._bootstrap(new_items)
        for item in new_items:
            self._symbol_timeframes.add(item)
        logger.info("WebSocketKlineFetcher 新增订阅: %d", len(new_items))

    async def unsubscribe(self, removed_items: set[tuple[str, str]]) -> None:
        """动态移除订阅。"""
        for item in removed_items:
            self._symbol_timeframes.discard(item)
            self._kline_caches.pop(item, None)
        logger.info("WebSocketKlineFetcher 移除订阅: %d", len(removed_items))

    @property
    def is_running(self) -> bool:
        """WebSocket 是否正在推送数据（连接活跃）。"""
        return self._connected
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/qinj057/develop/binance.transaction-ui && python3 -m pytest tests/test_kline_fetcher.py -v -k ws_fetcher`

Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add quant_trader/core/kline_fetcher.py tests/test_kline_fetcher.py
git commit -m "feat: implement WebSocketKlineFetcher

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: 改造 KlineDataManager 为调度层

**Files:**
- Modify: `quant_trader/core/kline_manager.py`
- Test: `tests/test_kline_manager.py`

**Interfaces:**
- Consumes: `WebSocketKlineFetcher`, `RESTKlineFetcher`（来自 Task 4 和 Task 5）
- Consumes: `DataStore`（用于 `_store_klines`）
- Produces: 改造后的 `KlineDataManager`：
  - `__init__(self, exchange, data_store, timeframes_seconds: dict[str, int], buffer_seconds: int = 1, proxy: str | None = None)`
  - `async start(self, symbol_timeframes: set[tuple[str, str]]) -> None`
  - `async update_subscriptions(self, new_set: set[tuple[str, str]]) -> None`（新增）
  - `subscribe(symbol, tf) -> asyncio.Queue`（Queue 消息格式改为 dict）
  - `get_klines(symbol, tf) -> list[dict] | None`
  - `get_realtime_price(symbol) -> float | None`（新增）
  - `async shutdown() -> None`

  Queue 消息格式：
  ```python
  {"klines": list[dict], "event": "update" | "closed"}
  ```

- [ ] **Step 1: 写失败测试**

写入 `tests/test_kline_manager.py`：

```python
"""KlineDataManager 集成测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from quant_trader.core.kline_manager import KlineDataManager


def _make_manager():
    """构造一个 KlineDataManager，注入模拟 exchange 和 data_store。"""
    exchange = MagicMock()
    exchange.fetch_ohlcv = AsyncMock(return_value=[])
    data_store = MagicMock()
    manager = KlineDataManager(
        exchange=exchange,
        data_store=data_store,
        timeframes_seconds={"5m": 300},
        buffer_seconds=0,
        proxy=None,
    )
    return manager


def test_subscribe_returns_queue():
    """subscribe 返回 asyncio.Queue。"""
    manager = _make_manager()
    queue = manager.subscribe("BTC/USDT", "5m")
    assert hasattr(queue, "get_nowait")


def test_get_realtime_price_returns_none_if_absent():
    """没有数据时 get_realtime_price 返回 None。"""
    manager = _make_manager()
    assert manager.get_realtime_price("BTC/USDT") is None


def test_on_kline_update_updates_realtime_price():
    """update 事件更新实时价格缓存。"""
    manager = _make_manager()
    klines = [
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1.0},
    ]
    manager._on_kline_update("BTC/USDT", "5m", klines, "update", "ws")
    assert manager.get_realtime_price("BTC/USDT") == 100.5


def test_on_kline_closed_stores_to_db():
    """closed 事件触发数据库写入。"""
    manager = _make_manager()
    klines = [
        {"timestamp": 2000, "open": 100, "high": 101, "low": 99, "close": 101.0, "volume": 1.0},
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.0, "volume": 1.0},
    ]
    manager._on_kline_update("BTC/USDT", "5m", klines, "closed", "ws")
    assert manager.data_store.insert_klines.called
    assert manager.get_realtime_price("BTC/USDT") == 101.0


def test_on_kline_ws_recovers_stops_rest():
    """WebSocket 恢复推送时，KlineDataManager 切回 ws 并停止 rest_fetcher。"""
    manager = _make_manager()
    manager._active_source = "rest"

    # 模拟 rest_fetcher 已启动
    manager.rest_fetcher = MagicMock()
    manager.rest_fetcher.stop = AsyncMock()

    klines = [{"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1.0}]
    manager._on_kline_update("BTC/USDT", "5m", klines, "update", "ws")

    assert manager._active_source == "ws"


def test_update_subscriptions_diffs_correctly():
    """update_subscriptions 正确计算新增和移除。"""
    manager = _make_manager()
    manager._symbol_timeframes = {("BTC/USDT", "5m"), ("ETH/USDT", "5m")}

    # 模拟 fetcher
    manager.ws_fetcher = MagicMock()
    manager.ws_fetcher.is_running = False  # WebSocket 未连接
    manager.ws_fetcher.subscribe = AsyncMock()
    manager.ws_fetcher.unsubscribe = AsyncMock()
    manager.rest_fetcher = MagicMock()
    manager.rest_fetcher.is_running = False
    manager.rest_fetcher.subscribe = AsyncMock()
    manager.rest_fetcher.unsubscribe = AsyncMock()

    new_set = {("BTC/USDT", "5m"), ("SOL/USDT", "5m")}  # 移除 ETH，新增 SOL
    asyncio.run(manager.update_subscriptions(new_set))

    assert manager._symbol_timeframes == new_set
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/qinj057/develop/binance.transaction-ui && python3 -m pytest tests/test_kline_manager.py -v`

Expected: FAIL，`TypeError: __init__() got an unexpected keyword argument 'timeframes_seconds'`

- [ ] **Step 3: 改造 KlineDataManager**

完全替换 `quant_trader/core/kline_manager.py` 内容：

```python
"""K 线数据管理器 - 协调 WebSocket 和 REST 两种 K 线获取方式。

设计目标：
1. WebSocket 优先，断连时降级到 REST，重连后切回
2. 维护 K 线缓存和实时价格缓存
3. 提供订阅接口（Queue），策略层无感知数据来源
4. 支持动态增减订阅（配置热重载）
"""

import asyncio
import logging

from .data_store import DataStore
from ..exchange.binance_adapter import BinanceAdapter
from .kline_fetcher import BaseKlineFetcher, RESTKlineFetcher, WebSocketKlineFetcher

logger = logging.getLogger(__name__)


class KlineDataManager:
    """K 线数据管理器 - 协调两个 fetcher，对外提供统一接口。"""

    def __init__(
        self,
        exchange: BinanceAdapter,
        data_store: DataStore,
        timeframes_seconds: dict[str, int],
        buffer_seconds: int = 1,
        proxy: str | None = None,
    ):
        self.exchange = exchange
        self.data_store = data_store
        self.buffer_seconds = buffer_seconds
        self.proxy = proxy

        # (symbol, timeframe) -> list[dict]  K 线缓存
        self._klines_cache: dict[tuple[str, str], list[dict]] = {}
        # symbol -> 实时价格
        self._realtime_prices: dict[str, float] = {}
        # (symbol, timeframe) -> list[asyncio.Queue]  订阅者
        self._subscribers: dict[tuple[str, str], list[asyncio.Queue]] = {}
        # 当前订阅的所有 (symbol, timeframe)
        self._symbol_timeframes: set[tuple[str, str]] = set()

        # 两个 fetcher
        self.ws_fetcher: WebSocketKlineFetcher | None = None
        self.rest_fetcher: RESTKlineFetcher | None = None
        # 当前数据来源："ws" | "rest"
        self._active_source: str = "ws"

    def _on_kline_update(
        self,
        symbol: str,
        timeframe: str,
        klines: list[dict],
        event: str,
        source: str,
    ) -> None:
        """Fetcher 回调入口：更新缓存、存数据库、通知订阅者。"""
        if not klines:
            return

        # 始终更新实时价格和 K 线缓存
        self._realtime_prices[symbol] = klines[0]["close"]
        self._klines_cache[(symbol, timeframe)] = klines

        # WebSocket 恢复推送 → 切回 ws，停止 rest
        if source == "ws" and self._active_source == "rest":
            self._active_source = "ws"
            logger.info("WebSocket 恢复，切回 ws 数据源")
            if self.rest_fetcher is not None:
                asyncio.create_task(self.rest_fetcher.stop())

        # closed 事件：存数据库
        if event == "closed":
            self._store_klines(klines, symbol, timeframe)

        # 通知订阅者
        key = (symbol, timeframe)
        for queue in self._subscribers.get(key, []):
            try:
                queue.put_nowait({"klines": klines, "event": event})
            except asyncio.QueueFull:
                pass

    async def start(self, symbol_timeframes: set[tuple[str, str]]) -> None:
        """启动 K 线管理：创建并启动 WebSocket fetcher。"""
        self._symbol_timeframes = set(symbol_timeframes)

        # 创建 fetcher
        self.ws_fetcher = WebSocketKlineFetcher(
            rest_exchange=self.exchange,
            on_kline=lambda sym, tf, kl, ev: self._on_kline_update(sym, tf, kl, ev, "ws"),
            on_fallback_to_rest=self._fallback_to_rest,
            timeframes_seconds=self._timeframes_seconds,
            proxy=self.proxy,
        )
        self.ws_fetcher._timeframes_seconds_holder = None  # placeholder，实际属性由构造函数传入

        # 启动 WebSocket fetcher
        await self.ws_fetcher.start(symbol_timeframes)
        logger.info("KlineDataManager 已启动，%d 个订阅", len(symbol_timeframes))

    def _fallback_to_rest(self) -> None:
        """WebSocket 3次重连失败时触发：启动 REST fetcher。"""
        if self.rest_fetcher is not None and self.rest_fetcher.is_running:
            return  # REST 已在运行
        logger.warning("降级到 REST K 线拉取")
        self._active_source = "rest"
        self.rest_fetcher = RESTKlineFetcher(
            exchange=self.exchange,
            on_kline=lambda sym, tf, kl, ev: self._on_kline_update(sym, tf, kl, ev, "rest"),
            timeframes_seconds=self._timeframes_seconds,
            buffer_seconds=self.buffer_seconds,
        )
        asyncio.create_task(self.rest_fetcher.start(self._symbol_timeframes))

    async def update_subscriptions(self, new_set: set[tuple[str, str]]) -> None:
        """配置热重载后更新订阅集合。"""
        added = new_set - self._symbol_timeframes
        removed = self._symbol_timeframes - new_set

        if not added and not removed:
            return

        # 更新 fetcher 订阅
        if self.ws_fetcher is not None and self.ws_fetcher.is_running:
            if added:
                await self.ws_fetcher.subscribe(added)
            if removed:
                await self.ws_fetcher.unsubscribe(removed)

        if self.rest_fetcher is not None and self.rest_fetcher.is_running:
            if added:
                await self.rest_fetcher.subscribe(added)
            if removed:
                await self.rest_fetcher.unsubscribe(removed)

        # 更新本地集合
        self._symbol_timeframes = set(new_set)

        # 清理被移除的缓存
        for key in removed:
            self._klines_cache.pop(key, None)
            self._subscribers.pop(key, None)

        logger.info("订阅已更新: 新增=%d, 移除=%d", len(added), len(removed))

    def subscribe(self, symbol: str, timeframe: str) -> asyncio.Queue:
        """订阅 K 线更新事件。返回 asyncio.Queue，消息格式：
            {"klines": list[dict], "event": "update" | "closed"}
        """
        key = (symbol, timeframe)
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        if key not in self._subscribers:
            self._subscribers[key] = []
        self._subscribers[key].append(queue)
        return queue

    def get_klines(self, symbol: str, timeframe: str) -> list[dict] | None:
        """获取当前缓存的 K 线数据（不触发拉取）。"""
        return self._klines_cache.get((symbol, timeframe))

    def get_realtime_price(self, symbol: str) -> float | None:
        """获取实时价格（来自 WebSocket 推送或 REST 最近一次拉取）。"""
        return self._realtime_prices.get(symbol)

    def _store_klines(self, klines: list[dict], symbol: str, timeframe: str):
        """将 K 线数据存入数据库（仅 closed 事件触发）。"""
        from datetime import datetime, timezone
        from .data_store import KlineRecord

        records = []
        for kline in klines:
            record = KlineRecord(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=datetime.fromtimestamp(kline["timestamp"] / 1000, tz=timezone.utc),
                open=kline["open"],
                high=kline["high"],
                low=kline["low"],
                close=kline["close"],
                volume=kline["volume"],
            )
            records.append(record)
        self.data_store.insert_klines(records)

    async def shutdown(self) -> None:
        """优雅关闭。"""
        if self.ws_fetcher is not None:
            await self.ws_fetcher.stop()
        if self.rest_fetcher is not None:
            await self.rest_fetcher.stop()
        logger.info("KlineDataManager 已关闭")
```

**重要**：上面代码中有一处错误——`self._timeframes_seconds` 在 `__init__` 中没有保存。需要在 `__init__` 中加入 `self._timeframes_seconds = timeframes_seconds`。修正后的 `__init__` 顶部应包含：

```python
def __init__(
    self,
    exchange: BinanceAdapter,
    data_store: DataStore,
    timeframes_seconds: dict[str, int],
    buffer_seconds: int = 1,
    proxy: str | None = None,
):
    self.exchange = exchange
    self.data_store = data_store
    self._timeframes_seconds = timeframes_seconds
    self.buffer_seconds = buffer_seconds
    self.proxy = proxy
    # ...（其余字段如上）
```

同时删除 `start()` 中的 `self.ws_fetcher._timeframes_seconds_holder = None` 这行（这是占位错误）。

实际实现时把 `start()` 中的 ws_fetcher 创建改为：

```python
self.ws_fetcher = WebSocketKlineFetcher(
    rest_exchange=self.exchange,
    on_kline=lambda sym, tf, kl, ev: self._on_kline_update(sym, tf, kl, ev, "ws"),
    on_fallback_to_rest=self._fallback_to_rest,
    timeframes_seconds=self._timeframes_seconds,
    proxy=self.proxy,
)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/qinj057/develop/binance.transaction-ui && python3 -m pytest tests/test_kline_manager.py -v`

Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add quant_trader/core/kline_manager.py tests/test_kline_manager.py
git commit -m "feat: refactor KlineDataManager to coordinate ws+rest fetchers

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: main.py 策略层适配 Queue 消息格式

**Files:**
- Modify: `quant_trader/main.py:48-58`（`__init__` 初始化 kline_manager 传 timeframes_seconds）
- Modify: `quant_trader/main.py:107-111`（`initialize()` 创建 KlineDataManager 时传入 TIMEFRAME_SECONDS 和 proxy）
- Modify: `quant_trader/main.py:125-128`（`_on_config_change` 增加订阅更新逻辑）
- Modify: `quant_trader/main.py:205-314`（`_run_strategy_cycle` 适配 Queue 消息格式）

**Interfaces:**
- Consumes: 改造后的 `KlineDataManager`（Queue 消息为 `{"klines": ..., "event": ...}`）
- Produces: 策略循环正确区分 update/closed 事件；配置热重载触发订阅更新

- [ ] **Step 1: 适配 KlineDataManager 创建**

修改 `quant_trader/main.py:107-111`，把：

```python
        # 初始化 K 线数据管理器
        self.kline_manager = KlineDataManager(
            self.exchange, self.data_store,
            buffer_seconds=exchange_cfg.kline_buffer_seconds,
        )
```

改为：

```python
        # 初始化 K 线数据管理器
        self.kline_manager = KlineDataManager(
            exchange=self.exchange,
            data_store=self.data_store,
            timeframes_seconds=TIMEFRAME_SECONDS,
            buffer_seconds=exchange_cfg.kline_buffer_seconds,
            proxy=exchange_cfg.proxy or None,
        )
```

- [ ] **Step 2: 适配配置热重载**

修改 `quant_trader/main.py:125-128`，把：

```python
    def _on_config_change(self, filename: str):
        """配置热重载回调，清空策略缓存。"""
        logger.info("配置已重新加载: %s", filename)
        self._strategy_cache = None  # 缓存失效，下次重建
```

改为：

```python
    def _on_config_change(self, filename: str):
        """配置热重载回调：清空策略缓存 + 更新 K 线订阅。"""
        logger.info("配置已重新加载: %s", filename)
        self._strategy_cache = None  # 缓存失效，下次重建

        # 仅 strategies.yaml 变更时才更新订阅
        if filename == "strategies.yaml" and self.kline_manager is not None:
            new_set: set[tuple[str, str]] = set()
            for strategy in self.config.strategies.strategies:
                if not strategy.enabled:
                    continue
                for symbol in strategy.symbols:
                    for tf in strategy.timeframes:
                        new_set.add((symbol, tf))
            asyncio.create_task(self.kline_manager.update_subscriptions(new_set))
```

- [ ] **Step 3: 适配策略循环的 Queue 消息格式**

修改 `quant_trader/main.py:214-314`（`_run_strategy_cycle` 的 while 循环体），把：

```python
                # 等待 K 线更新通知（最多等待 interval 秒）
                try:
                    klines = await asyncio.wait_for(kline_queue.get(), timeout=interval)
                except asyncio.TimeoutError:
                    logger.debug("等待 K 线更新超时: %s %s", symbol, timeframe)
                    continue

                if not klines:
                    logger.warning("收到空 K 线数据: %s %s", symbol, timeframe)
                    continue

                # 更新模拟持仓价格（触发止损检查）—— 始终使用最新价保持实时性
                current_price = klines[0]["close"]  # klines[0] 是当前正在形成的K线
                self.paper_engine.update_prices(symbol, current_price, self.data_store)

                # 判断最新K线是否已完成（用于决定是否执行策略）
                # klines[0] 是当前正在形成的K线，klines[1] 是上一根已收盘K线
                # K线时间戳是开盘时间，收盘时间 = 开盘时间 + interval
                if len(klines) < 2:
                    logger.debug("K线数据不足，跳过策略执行: %s %s", symbol, timeframe)
                    continue

                last_kline_open_ms = klines[1]["timestamp"]
                last_kline_close_ms = last_kline_open_ms + interval * 1000
                now_ms = int(_time.time() * 1000)
                buffer_ms = getattr(self.kline_manager, 'buffer_seconds', 1) * 1000
                # 给交易所数据留buffer_seconds缓冲，确保K线已完全形成
                kline_fully_formed = now_ms >= last_kline_close_ms + buffer_ms

                # 执行策略 — 只在K线完成后执行，确保volume等数据完整
                strategy = self._get_strategy(strategy_name)
                if strategy and kline_fully_formed:
                    # 获取上级周期 K 线（多周期趋势确认）
                    parent_tf = PARENT_TIMEFRAME.get(timeframe)
                    parent_klines = self.kline_manager.get_klines(symbol, parent_tf) if parent_tf else None
                    # 获取当前 funding rate（带缓存）
                    funding_rate = await self._get_funding_rate(symbol)
                    # 排除当前正在形成的K线，策略只跑在已收盘K线上
                    signals = strategy.on_bar(symbol, timeframe, klines[1:],
                                              self.paper_engine.positions.values(),
                                              parent_klines=parent_klines,
                                              funding_rate=funding_rate)
                    now = _time.time()
                    for signal in signals:
                        last = self._last_signal_time.get(signal.symbol, 0)
                        if now - last < self._signal_cooldown:
                            logger.debug("跳过冷却期内的信号: %s %s", signal.symbol, signal.signal_type)
                            continue
                        await self._handle_signal(signal)
                        self._last_signal_time[signal.symbol] = now
                        break  # 每根K线只处理一个信号，与回测保持一致
                elif strategy and not kline_fully_formed:
                    buffer_s = getattr(self.kline_manager, 'buffer_seconds', 1)
                    logger.debug(
                        "跳过策略执行 %s %s: 上一根K线尚未完成 (开盘=%s, 需>=收盘+%ds=%s)",
                        symbol, timeframe,
                        datetime.fromtimestamp(last_kline_open_ms / 1000, tz=timezone.utc).strftime("%H:%M:%S"),
                        buffer_s,
                        datetime.fromtimestamp((last_kline_close_ms + buffer_s * 1000) / 1000, tz=timezone.utc).strftime("%H:%M:%S"),
                    )
```

改为：

```python
                # 等待 K 线更新通知（最多等待 interval 秒）
                try:
                    msg = await asyncio.wait_for(kline_queue.get(), timeout=interval)
                except asyncio.TimeoutError:
                    logger.debug("等待 K 线更新超时: %s %s", symbol, timeframe)
                    continue

                klines = msg["klines"]
                event = msg["event"]
                if not klines:
                    logger.warning("收到空 K 线数据: %s %s", symbol, timeframe)
                    continue

                # 始终更新模拟持仓价格（触发止损检查）
                current_price = klines[0]["close"]
                self.paper_engine.update_prices(symbol, current_price, self.data_store)

                # 只有 closed 事件才执行策略（K 线已收盘）
                if event != "closed":
                    continue

                if len(klines) < 2:
                    logger.debug("K线数据不足，跳过策略执行: %s %s", symbol, timeframe)
                    continue

                # 执行策略 — 排除当前正在形成的K线
                strategy = self._get_strategy(strategy_name)
                if strategy:
                    # 获取上级周期 K 线（多周期趋势确认）
                    parent_tf = PARENT_TIMEFRAME.get(timeframe)
                    parent_klines = self.kline_manager.get_klines(symbol, parent_tf) if parent_tf else None
                    # 获取当前 funding rate（带缓存）
                    funding_rate = await self._get_funding_rate(symbol)
                    # klines[0] 是刚收盘的K线，klines[1:] 是更早的历史
                    signals = strategy.on_bar(symbol, timeframe, klines,
                                              self.paper_engine.positions.values(),
                                              parent_klines=parent_klines,
                                              funding_rate=funding_rate)
                    now = _time.time()
                    for signal in signals:
                        last = self._last_signal_time.get(signal.symbol, 0)
                        if now - last < self._signal_cooldown:
                            logger.debug("跳过冷却期内的信号: %s %s", signal.symbol, signal.signal_type)
                            continue
                        await self._handle_signal(signal)
                        self._last_signal_time[signal.symbol] = now
                        break  # 每根K线只处理一个信号，与回测保持一致
```

**注意**：原代码策略执行用 `klines[1:]`（排除当前正在形成的K线）。改造后 closed 事件意味着 `klines[0]` 已是刚收盘的 K 线，所以应传 `klines`（包含刚收盘的）而不是 `klines[1:]`。但这与回测一致性有关——回测时代码注释明确说"排除当前正在形成的K线，策略只跑在已收盘K线上"。

由于现在 closed 事件本身表示 klines[0] 已收盘，且策略函数 `on_bar` 通常期望 `klines[0]` 是最近一根收盘的（参考 s_r_levels.py 的使用方式），传 `klines` 是正确的。如果策略函数内部还假设 klines[0] 是正在形成的、klines[1:] 是已收盘的，则需要单独适配策略函数。**实施时需要检查 `strategy.on_bar` 的实现**，确认它期望 klines[0] 是已收盘还是正在形成。如果策略假设 klines[0] 是正在形成的，则保留原行为：传 `klines[1:]`，并在 klines 前插入一个"虚拟的当前正在形成的 K 线"（与刚收盘的相同）。最简单的做法是保持 `klines[1:]` 不变，但这样会跳过刚收盘的那根 K 线的策略触发，不符合设计意图。

**推荐做法**：检查策略实现，如果 `on_bar` 把 klines[0] 当作"刚收盘的"，传 `klines`；如果当作"正在形成的"，传 `klines[1:]` 但需补一根虚拟的当前 K 线到前面。实施此任务前先读 `strategies/s_r_levels.py` 和 `strategies/bollinger_bands.py` 的 `on_bar` 方法确认行为。

- [ ] **Step 4: 检查策略 on_bar 的 klines 假设**

Run:
```bash
cd /Users/qinj057/develop/binance.transaction-ui
grep -n "def on_bar" quant_trader/strategies/s_r_levels.py quant_trader/strategies/bollinger_bands.py
grep -n "klines\[0\]" quant_trader/strategies/s_r_levels.py quant_trader/strategies/bollinger_bands.py | head -20
```

根据 klines[0] 的使用方式决定传 `klines` 还是 `klines[1:]`。预期：策略内部把 klines[0] 当作"刚收盘的最新K线"（与回测逻辑一致），因此传 `klines` 是正确的。

如果确认策略把 klines[0] 视为"刚收盘的"，则 Step 3 的代码 `signals = strategy.on_bar(symbol, timeframe, klines, ...)` 是正确的。

如果策略把 klines[0] 视为"正在形成的"（即旧 REST 行为的延续），则需要改为：
```python
# klines[0] 是刚收盘的，构造一个"虚拟正在形成"的前置项
virtual_current = [klines[0]] + klines  # 让原 klines[0] 变成 klines[1]
signals = strategy.on_bar(symbol, timeframe, virtual_current, ...)
```
但这违反直觉，不推荐。优先检查并按"传 klines"实施。

- [ ] **Step 5: 运行现有测试确保不回归**

Run:
```bash
cd /Users/qinj057/develop/binance.transaction-ui
python3 -m pytest tests/ -v
```

Expected: 所有现有测试通过

- [ ] **Step 6: 手动运行引擎冒烟测试（可选，需要 Binance 连接）**

```bash
cd /Users/qinj057/develop/binance.transaction-ui
timeout 60 python3 -m quant_trader.main 2>&1 | head -100
```

Expected: 看到日志 "WebSocket bootstrap 完成: BTC/USDT 5m (151 根)"，"KlineDataManager 已启动"，无 Python 异常。

注意：如果当前没有 Binance 连接（如无代理或网络问题），bootstrap 会失败但应优雅降级到 REST。

- [ ] **Step 7: 提交**

```bash
git add quant_trader/main.py
git commit -m "feat: adapt strategy cycle to new kline message format

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: 修复 REST 实时价格 bug（serialize_status.py）

**Files:**
- Modify: `quant_trader/core/serialization.py:34-42, 56-78`

**Interfaces:**
- Consumes: `KlineDataManager.get_realtime_price(symbol)`
- Produces: `serialize_status` 优先用 kline_manager 的实时价格，回退到 REST 请求

- [ ] **Step 1: 修改 _fetch_realtime_price 为可选**

修改 `quant_trader/core/serialization.py:34-42`，把：

```python
async def _fetch_realtime_price(exchange, symbol: str) -> float | None:
    """获取币种实时价格（取 1m 最新收盘价）。"""
    try:
        klines = await exchange.fetch_ohlcv(symbol, "1m", limit=1)
        if klines:
            return klines[0]["close"]
    except Exception:
        logger.warning("获取 %s 实时价格失败", symbol, exc_info=True)
    return None
```

改为：

```python
async def _fetch_realtime_price(exchange, symbol: str) -> float | None:
    """获取币种实时价格（取 1m 最新收盘价，作为降级后备）。"""
    try:
        klines = await exchange.fetch_ohlcv(symbol, "1m", limit=1)
        if klines:
            return klines[0]["close"]
    except Exception:
        logger.warning("获取 %s 实时价格失败", symbol, exc_info=True)
    return None


def _get_realtime_price(engine, symbol: str) -> float | None:
    """从 kline_manager 缓存获取实时价格，无缓存时返回 None。"""
    kline_manager = getattr(engine, "kline_manager", None)
    if kline_manager is None:
        return None
    return kline_manager.get_realtime_price(symbol)
```

- [ ] **Step 2: 修改 serialize_status 优先用缓存价格**

修改 `quant_trader/core/serialization.py:60-78`，把：

```python
    # 持仓序列化：实时查询价格，重新计算未实现盈亏
    positions = {}
    realtime_unrealized_pnl: dict[str, float] = {}
    for symbol, pos in paper.positions.items():
        pos_dict = serialize_position(pos)
        current_price = pos.current_price

        if engine.exchange:
            realtime_price = await _fetch_realtime_price(engine.exchange, symbol)
            if realtime_price is not None:
                current_price = realtime_price
                pos_dict["current_price"] = current_price
                if pos.side == "buy":
                    unrealized = (current_price - pos.entry_price) * pos.quantity
                else:
                    unrealized = (pos.entry_price - current_price) * pos.quantity
                pos_dict["unrealized_pnl"] = unrealized

        positions[symbol] = pos_dict
        realtime_unrealized_pnl[symbol] = pos_dict.get("unrealized_pnl", 0.0)
```

改为：

```python
    # 持仓序列化：优先用 kline_manager 实时价格缓存，回退到 REST 请求
    positions = {}
    realtime_unrealized_pnl: dict[str, float] = {}
    for symbol, pos in paper.positions.items():
        pos_dict = serialize_position(pos)
        current_price = pos.current_price

        # 优先用缓存（WebSocket/REST 推送更新）
        cached_price = _get_realtime_price(engine, symbol)
        if cached_price is not None:
            realtime_price: float | None = cached_price
        elif engine.exchange:
            realtime_price = await _fetch_realtime_price(engine.exchange, symbol)
        else:
            realtime_price = None

        if realtime_price is not None:
            current_price = realtime_price
            pos_dict["current_price"] = current_price
            if pos.side == "buy":
                unrealized = (current_price - pos.entry_price) * pos.quantity
            else:
                unrealized = (pos.entry_price - current_price) * pos.quantity
            pos_dict["unrealized_pnl"] = unrealized

        positions[symbol] = pos_dict
        realtime_unrealized_pnl[symbol] = pos_dict.get("unrealized_pnl", 0.0)
```

- [ ] **Step 3: 提交**

```bash
git add quant_trader/core/serialization.py
git commit -m "fix: use cached realtime price in serialize_status

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 9: 修复 REST 实时价格 bug（query_server.py）

**Files:**
- Modify: `quant_trader/core/query_server.py:142, 274`

**Interfaces:**
- Consumes: `KlineDataManager.get_realtime_price(symbol)`
- Produces: S/R 查询的 current_price 优先用实时缓存，回退到 K 线 cache

- [ ] **Step 1: 修改 query_server 的 current_price 来源**

修改 `quant_trader/core/query_server.py:142`，把：

```python
    # 计算手动交易建议
    current_price = analysis["current_price"]
    atr_val = analysis["atr"]
```

改为：

```python
    # 计算手动交易建议
    # 优先用 kline_manager 的实时价格缓存（避免 K 线 cache 滞后）
    current_price = engine.kline_manager.get_realtime_price(symbol)
    if current_price is None:
        current_price = analysis["current_price"]  # 回退到 K 线 cache
    atr_val = analysis["atr"]
```

注意：`analysis["current_price"]` 仍来自 `analyze_sr_levels(klines)`，内部用 `klines[0]["close"]`，作为回退值。S/R 位计算仍基于 K 线数据（这是正确的，技术分析用历史 K 线），但 `current_price` 用于计算"距离当前价的百分比"等指标，应该用实时价。

- [ ] **Step 2: 检查 response["current_price"] 字段**

`query_server.py:274` 的 `response` 字典中 `"current_price": current_price` 已经是改造后的实时价，无需再改。

- [ ] **Step 3: 运行所有测试确保无回归**

Run:
```bash
cd /Users/qinj057/develop/binance.transaction-ui
python3 -m pytest tests/ -v
```

Expected: 所有测试通过

- [ ] **Step 4: 提交**

```bash
git add quant_trader/core/query_server.py
git commit -m "fix: use realtime price cache in S/R query

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 10: 端到端集成测试与收尾

**Files:**
- Test: `tests/test_kline_fetcher.py`（追加）

**Interfaces:**
- 验证整个数据流的协同工作

- [ ] **Step 1: 追加端到端测试**

追加到 `tests/test_kline_fetcher.py`：

```python
def test_full_pipeline_rest_only():
    """端到端：REST 模式下，KlineDataManager 收到 closed 事件并更新价格。"""
    from quant_trader.core.kline_manager import KlineDataManager
    from unittest.mock import MagicMock

    exchange = MagicMock()
    exchange.fetch_ohlcv = AsyncMock(return_value=[
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1.0},
    ])
    data_store = MagicMock()
    manager = KlineDataManager(
        exchange=exchange,
        data_store=data_store,
        timeframes_seconds={"5m": 300},
        buffer_seconds=0,
        proxy=None,
    )

    # 模拟 REST fetcher 直接回调
    klines = [
        {"timestamp": 2000, "open": 100, "high": 101, "low": 99, "close": 101.0, "volume": 1.0},
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.0, "volume": 1.0},
    ]
    manager._on_kline_update("BTC/USDT", "5m", klines, "closed", "rest")

    # 实时价格已更新
    assert manager.get_realtime_price("BTC/USDT") == 101.0
    # K 线 cache 已更新
    assert manager.get_klines("BTC/USDT", "5m") == klines
    # 数据库已写入
    assert data_store.insert_klines.called


def test_full_pipeline_ws_then_rest_fallback():
    """端到端：WebSocket 降级到 REST，再恢复切回 ws。"""
    from quant_trader.core.kline_manager import KlineDataManager
    from unittest.mock import MagicMock

    exchange = MagicMock()
    exchange.fetch_ohlcv = AsyncMock(return_value=[])
    data_store = MagicMock()
    manager = KlineDataManager(
        exchange=exchange,
        data_store=data_store,
        timeframes_seconds={"5m": 300},
        buffer_seconds=0,
        proxy=None,
    )
    manager._symbol_timeframes = {("BTC/USDT", "5m")}

    # 1. 触发降级
    manager._fallback_to_rest()
    assert manager._active_source == "rest"
    assert manager.rest_fetcher is not None

    # 2. 模拟 REST 数据回调
    klines_rest = [{"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.0, "volume": 1.0}]
    manager._on_kline_update("BTC/USDT", "5m", klines_rest, "closed", "rest")
    assert manager.get_realtime_price("BTC/USDT") == 100.0

    # 3. WebSocket 恢复，推送 update 事件 → 应切回 ws
    klines_ws = [{"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1.0}]
    manager._on_kline_update("BTC/USDT", "5m", klines_ws, "update", "ws")
    assert manager._active_source == "ws"
    assert manager.get_realtime_price("BTC/USDT") == 100.5  # ws 数据覆盖了
```

- [ ] **Step 2: 运行所有测试**

Run:
```bash
cd /Users/qinj057/develop/binance.transaction-ui
python3 -m pytest tests/ -v
```

Expected: 所有测试通过

- [ ] **Step 3: 更新 requirements.txt（如需要）**

ccxt.pro 已包含在 ccxt 4.5.56 中（`import ccxt.pro` 即可），无需额外依赖。检查 `requirements.txt` 无需修改。

- [ ] **Step 4: 手动冒烟测试（可选）**

```bash
cd /Users/qinj057/develop/binance.transaction-ui
timeout 120 python3 -m quant_trader.main 2>&1 | tail -50
```

Expected: 引擎启动、WebSocket bootstrap 成功、K 线推送正常、status 接口可访问。

- [ ] **Step 5: 提交**

```bash
git add tests/test_kline_fetcher.py
git commit -m "test: add end-to-end integration tests for kline pipeline

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

- [ ] **Step 6: 创建 PR**

```bash
git push -u origin feature/websocket-kline-fetcher
gh pr create --title "feat: WebSocket K 线数据获取与 REST 降级" --body "$(cat <<'EOF'
## Summary
- 新增 WebSocketKlineFetcher（基于 ccxt.pro），替代 REST 轮询作为主数据源
- REST 作为降级后备：3次快速重连失败后自动切换，WebSocket 恢复后切回
- 后台持续重连 WebSocket（10min/30min 间隔），无需人工干预
- 新增实时价格缓存，修复 REST 模式下实时价格滞后 bug
- 配置热重载支持动态增减 K 线订阅

## Test plan
- [x] 单元测试：BaseKlineFetcher / merge_kline_update / RESTKlineFetcher / WebSocketKlineFetcher
- [x] 集成测试：KlineDataManager 协调 ws+rest、降级与恢复切换
- [x] 端到端测试：完整数据流（REST 模式、ws→rest→ws 切换）
- [ ] 手动冒烟：连接 Binance 验证 WebSocket 推送、降级、恢复

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Notes

**Spec coverage:**
- ✅ WebSocket 推送替代 REST 轮询 (Task 5)
- ✅ 3次重连 → 降级 REST (Task 5 `_handle_disconnect`)
- ✅ 后台持续重连，恢复后切回 (Task 5 + Task 6 `_on_kline_update` ws 分支)
- ✅ 实时价格缓存 (Task 6 `_realtime_prices`)
- ✅ 保持策略层接口不变 (Task 6 `subscribe`/`get_klines` 签名不变)
- ✅ 配置热重载订阅更新 (Task 6 `update_subscriptions` + Task 7 `_on_config_change`)
- ✅ REST 实时价格 bug 修复 (Task 8 + Task 9)
- ✅ K 线收盘判断（K 线切换检测）(Task 3 `merge_kline_update` + Task 5 `_handle_ws_message`)
- ✅ Queue 消息格式变更 (Task 6 + Task 7)

**Placeholder scan:** 已检查，无 TBD/TODO。

**Type consistency:**
- `BaseKlineFetcher.on_kline` 签名一致：`(symbol, timeframe, klines, event)`
- `merge_kline_update` 返回 `(merged_list, event)`，所有调用一致
- `KlineDataManager` 构造函数签名在 Task 6 定义，Task 7 调用一致
- `get_realtime_price(symbol)` 在 Task 6 定义，Task 8/9 调用一致

**已知风险：**
- Task 7 Step 3 中策略 `on_bar` 的 klines 假设需要在实施时验证（已提供验证步骤）
- Task 5 的 `_handle_ws_message` 假设 `candles[-1]` 是最新 K 线，需在手动冒烟测试时确认
- ccxt.pro 的代理配置（`wsProxy`/`wssProxy`）在不同版本可能不同，实施时验证
