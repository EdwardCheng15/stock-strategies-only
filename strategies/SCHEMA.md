# 策略 JSON Schema

每個策略檔案 = 一個 `.json`，檔名 = `{id}.json`。

## 頂層欄位

| 欄位 | 型別 | 必填 | 說明 |
| --- | --- | --- | --- |
| `id` | string | ✅ | 策略唯一 ID（建議用 slug 或 UUID）|
| `name` | string | ✅ | 顯示名稱 |
| `description` | string |   | 給人看的描述 |
| `source` | `"default" \| "manual" \| "ai"` |   | 來源；前端用來顯示徽章 |
| `created_at` / `updated_at` | ISO 8601 |   | 時間戳 |
| `params` | object | ✅ | 實際策略參數，扁平 dict（見下） |

## `params` 欄位

### 基本面門檻

- `eps_threshold` (number) — 最近兩年 EPS 最小值需 > 此數
- `roe_threshold` (number) — 最近兩年 ROE 最小值需 > 此數 (%)
- `fundamental_pass_required` (bool) — 是否強制基本面要通過才能 BUY

### 回測 & 訊號

- `backtest_years` (int) — 回測年數
- `min_tech_score_for_signal` (int 0-100) — 回測時，技術分達多少算一次訊號（實際取與 `min_tech_score_for_buy` 較大者）
- `round_trip_cost` (0-1) — 來回交易成本，預設 0.0047

### 停損與出場（預設：ATR 停損＋移動停利月線版）

- `stop_mode` (`"atr"` \| `"fixed"`) — 預設 `atr`：停損 = `atr_stop_mult` × ATR14 ÷ 收盤
- `atr_stop_mult` (number) — 預設 2.5
- `stop_min_pct` / `stop_max_pct` (0-1) — ATR 停損上下限，預設 0.04 / 0.15
- `stop_loss` (0-1) — 固定停損；`stop_mode=fixed` 時使用，ATR 資料不足時當備援
- `exit_mode` (`"trailing"` \| `"fixed"`) — 預設 `trailing`：與持倉健檢一致的移動停利
- `breakeven_trigger_pct` (0-1) — 收盤波段高點達此漲幅後停損上移到成本，預設 0.06
- `exit_ma_days` (int) — 保本後收盤跌破此均線停利，預設 20（月線）
- `max_hold_days` (int) — 最長持有天數，預設 120
- `hold_days` / `target_return` — 僅 `exit_mode=fixed` 時使用（固定持有天數／固定停利）

### 進場濾網與 BUY 放行

- `max_gap_up_pct` (0-1) — 隔天開盤跳空超過此幅度不進場（盤前清單的放棄價），如 0.035
- `max_ma5_bias_pct` (%) — 收盤距 5 日線乖離上限，超過扣 6 分
- `max_chg_5d_pct` (%) — 5 日漲幅上限，超過 BUY→WATCH，預設 15
- `min_backtest_winrate` (0-1) / `min_backtest_samples` (int) — 回測勝率門檻，預設 0.52 / 10 筆
- `v34_factors` (object) / `v34_weight` (0-1) — V3.4 多因子與權重

### 評分加權

- `weight_fundamental` / `weight_technical` / `weight_backtest` (0-1) — 三者加總應 = 1
- `min_total_score_for_buy` (0-100) — 總分達多少才考慮 BUY
- `min_tech_score_for_buy` (0-100) — 技術分至少要達多少才考慮 BUY

### 技術訊號開關（影響評分）

- `use_ma_alignment` (bool) — 均線多頭排列
- `use_bollinger_bounce` (bool) — 布林下軌反彈（多頭中買回檔）
- `use_kd_golden_cross` (bool) — KD 黃金交叉
- `use_macd_bullish` (bool) — MACD 多頭
- `use_volume_patterns` (bool) — 量價型態加減分

### 大盤濾鏡

- `market_filter_enabled` (bool) — 加權指數跌破月線時 BUY 扣 6 分、曝險上限降到 40%；回測中該日不進場
- `market_filter_ma_period` (int) — 月線天數
