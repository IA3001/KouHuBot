# 计划 4：数据模型与迁移

> 状态：设计已锁定，待开工
> 依赖：计划 1（积分制）、2（题目拉取）、3（判题）

## 产品形态

```
无定时器 + 加权积分榜

全群做同一道题
玩家 `join` 报名，只有报名的人计分
`new` 派题 → 首个 AC 或任意 `g` 关闭当前题 → 需要再发 `new`
群难度随集体进度爬升（每档 20 道）
```

## 两个库

### `cf_cache.db`（全局，跨群共享）

题面是资产，不能跟着表结构一起被清空。

```sql
CREATE TABLE cf_problems (
    cf_key        TEXT PRIMARY KEY,   -- "1234D"
    contest_id    INTEGER,
    idx           TEXT,
    rating        INTEGER,
    tier          INTEGER,            -- 档次，如 1600
    points        INTEGER,            -- 该档每题分值，如 450
    title         TEXT,
    statement_en  TEXT,               -- 保留 $$$ 公式标记
    statement_cn  TEXT,
    examples_json TEXT,
    time_limit    REAL,
    memory_limit  INTEGER,
    fetched_at    REAL,
    translated_at REAL,
    used_count    INTEGER DEFAULT 0   -- 被派过几次，用于去重
);
CREATE INDEX idx_cf_rating ON cf_problems(rating);
CREATE INDEX idx_cf_tier   ON cf_problems(tier);
```

### `kouhu_oj.db`（按群隔离）

```sql
CREATE TABLE players (
    scope_id     TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    user_name    TEXT,
    total_score  INTEGER DEFAULT 0,   -- 累计积分
    solved       INTEGER DEFAULT 0,   -- 解出题数
    first_try    INTEGER DEFAULT 0,   -- 首次提交即 AC 的次数
    attempts     INTEGER DEFAULT 0,   -- 总提交次数
    gave_up      INTEGER DEFAULT 0,   -- 放弃次数（用于识别捣乱）
    joined_at    REAL,
    active       INTEGER DEFAULT 1,
    PRIMARY KEY (scope_id, user_id)
);

-- 每群当前题，没有轮次概念
CREATE TABLE current_problem (
    scope_id     TEXT PRIMARY KEY,
    cf_key       TEXT NOT NULL,
    tier         INTEGER,
    points       INTEGER,
    status       TEXT DEFAULT 'open',  -- open / ac / gave_up
    opened_by    TEXT,
    opened_at    REAL,
    closed_at    REAL,
    closed_by    TEXT
);

-- 每群难度进度
CREATE TABLE group_state (
    scope_id     TEXT PRIMARY KEY,
    current_tier INTEGER DEFAULT 1200,
    tier_count   INTEGER DEFAULT 0,    -- 当前档已派了几道
    updated_at   REAL
);

CREATE TABLE attempts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id  TEXT NOT NULL,
    cf_key    TEXT NOT NULL,
    user_id   TEXT NOT NULL,
    n         INTEGER NOT NULL,        -- 该题第几次提交
    verdict   TEXT NOT NULL,           -- AC / WA
    at        REAL NOT NULL
);

CREATE TABLE solved (
    scope_id  TEXT NOT NULL,
    user_id   TEXT NOT NULL,
    cf_key    TEXT NOT NULL,
    points    INTEGER NOT NULL,
    solved_at REAL NOT NULL,
    PRIMARY KEY (scope_id, user_id, cf_key)   -- 同一题只计一次
);
```

## 迁移

`SCHEMA_VERSION` 4 → 5，DROP 旧表，按新结构重建。
**旧数据全部丢弃**（已确认）。`cf_cache.db` 不受影响。

## 待处理

- 判题并发：连发两条 `s` 会导致判题并发、`solved` 重复写入。需要 per-user 锁
- `g` 捣乱：任何人 `g` 都能关闭当前题。记录 `players.gave_up`，榜上可见
- 判题原子性：判题成功后在一个事务里更新 attempts + solved + players + assignments
- 备份：SQLite WAL 模式直接 cp 不一致，需 `sqlite3 .backup`
