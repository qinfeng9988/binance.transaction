## 为什么

当前量化交易系统在加仓场景下无法精确归因：多次买入信号触发加仓后，持仓合并为加权均价，平仓（止盈/止损/部分平仓）时无法追溯每笔入场信号对应的盈亏。这导致无法评估单个信号的质量，也无法判断哪笔入场时机更优或更差。

## 变更内容

- **新增** `position_entries` 表，记录每笔入场明细（数量、价格、关联信号 ID）
- **新增** `position_entry_closures` 表，记录每笔出场明细（平仓数量、入场价、出场价、归因盈亏、出场原因）
- **修改** `paper_trading.py`，开仓/加仓时写入 entry，平仓/部分平仓时按 FIFO 扣减 entries 并写入 closure 记录
- **修改** `data_store.py`，新增两张表的建表语句和 CRUD 方法
- **新增** 归因查询接口，支持按信号、按持仓、按时间段查询归因盈亏

## 功能 (Capabilities)

### 新增功能
- `trade-attribution`: 交易归因系统，将每笔平仓盈亏精确追溯到触发入场的信号

### 修改功能
- `data-storage`: 新增 position_entries 和 position_entry_closures 两张表及对应数据访问方法
- `paper-trading`: 开仓/加仓/平仓/部分平仓流程中同步维护 entry 和 closure 记录

## 影响

- **数据库变更**: 新增两张表，不修改现有表结构，向后兼容
- **运行时逻辑**: positions 表的合并持仓逻辑不变，entries/closures 是附加的归因层
- **手续费分摊**: 部分平仓时，手续费按各 entry 被扣减数量的比例分摊到每条 closure 记录
- **FIFO 规则**: 平仓时按入场顺序（seq）先进先出扣减 entries
