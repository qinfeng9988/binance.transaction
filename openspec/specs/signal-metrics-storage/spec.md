## 新增需求

### 需求:系统必须将信号指标字段存储到 signals 表
系统必须在 signals 表中存储 12 个策略指标字段，从 Signal metadata 提升为显式数据库列，禁止仅依赖 metadata dict 传递指标数据。

字段清单：
- `quality_score` NUMERIC(10,2)：信号最终质量评分
- `adx` NUMERIC(10,2)：信号生成时 ADX 值
- `plus_di` NUMERIC(10,2)：信号生成时 +DI 值
- `minus_di` NUMERIC(10,2)：信号生成时 -DI 值
- `rsi` NUMERIC(10,2)：信号生成时 RSI 值
- `volume_ratio` NUMERIC(10,2)：成交量比率
- `parent_trend` VARCHAR(20)：上级周期趋势方向（uptrend/downtrend/ranging/neutral）
- `direction_type` VARCHAR(20)：信号方向类型（main=顺势，secondary=逆势）
- `is_counter_trend` BOOLEAN：是否逆势信号
- `atr` NUMERIC(20,8)：信号生成时 ATR 值
- `entry_threshold_pct` NUMERIC(10,4)：当次实际生效的入场距离阈值
- `distance_pct` NUMERIC(10,4)：实际入场距离 S/R 位的百分比

所有字段均可为 NULL（Bollinger 策略等不产出这些指标时为 NULL）。

#### 场景:S/R 策略信号写入时指标落库
- **当** S/R 策略生成 buy/sell 信号
- **那么** 系统必须将 12 个指标字段写入 signals 表对应列，值从策略计算结果获取

#### 场景:Bollinger 策略信号写入时缺失指标
- **当** Bollinger 策略生成信号（仅有 rsi、atr）
- **那么** rsi 和 atr 写入对应列，其余 10 个字段为 NULL

#### 场景:指标字段禁止通过 metadata 查询
- **当** 归因分析或查询需要信号指标
- **那么** 系统必须从 signals 表的显式列读取，禁止反序列化 metadata

### 需求:Signal 模型必须包含 12 个指标字段
系统必须在 Signal dataclass 中新增 12 个可选字段，策略构造 Signal 时显式赋值。

#### 场景:策略构造信号时赋值指标
- **当** 策略生成 Signal 对象
- **那么** 策略必须从计算结果中显式赋值 quality_score、adx、plus_di、minus_di、rsi、volume_ratio、parent_trend、direction_type、is_counter_trend、atr、entry_threshold_pct、distance_pct

#### 场景:策略未赋值的指标字段
- **当** 策略未为某个指标字段赋值
- **那么** 该字段默认为 None，insert_signal 时写入 NULL
