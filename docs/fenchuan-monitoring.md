# 纷传圈子监控运行说明

## 当前运行模式

当前采用**稳定优先**方案：不使用 wrapper，直接由 Hermes cron 调度 `sx_monitor.py`，并按北京时间（UTC+8）分时段执行。

### 任务 A：UTC+8 12:00-19:00 每 2 小时执行一次

- Hermes cron 任务：`纷传圈子监控（UTC+8 12-19 每2小时）`
- `job_id`：`968b71ee59d3`
- 调度：`0 4,6,8,10 * * *`（UTC）
- 对应北京时间：`12:00`、`14:00`、`16:00`、`18:00`
- 执行脚本：`sx_monitor.py`
- 运行模式：`no_agent=true`

### 任务 B：UTC+8 19:00-24:00 每 30 分钟执行一次

- Hermes cron 任务：`纷传圈子监控（UTC+8 19-24 每30分钟）`
- `job_id`：`998fa8b31214`
- 调度：`0,30 11-15 * * *`（UTC）
- 对应北京时间：`19:00`、`19:30`、`20:00`、`20:30`、`21:00`、`21:30`、`22:00`、`22:30`、`23:00`、`23:30`
- 执行脚本：`sx_monitor.py`
- 运行模式：`no_agent=true`

> 说明：cron 表达式不能写 `24:00`，因此“19-24 点每 30 分钟”在实现上表示北京时间 `19:00` 到 `23:30`，`00:00` 不执行。

这意味着：
- **有新内容**：脚本打印完整用户可读消息，由 Hermes 直接投递
- **无新内容**：脚本静默退出，不发送任何消息
- **token/API 故障**：脚本打印显式告警，Hermes 会投递故障信息
- **其他时间**：不运行

## 为什么不用 wrapper

纷传监控历史上曾使用 `sx_monitor_wrapper.py` 做时间门控，但这会引入额外漂移面：

- cron 配置与真实执行逻辑可能不一致
- wrapper 可能让 `no_agent` 输出语义变得不透明
- 排障时容易出现“看起来成功，用户却没收到消息”的假健康状态

因此当前推荐做法是：

- cron **直接指向主脚本** `sx_monitor.py`
- 时间策略直接由 cron schedule 表达
- 保持脚本输出语义简单明确

## 依赖与数据面

### SQLite
路径：`/root/sx_articles.db`

作用：
- 作为去重和基础持久层
- 新记录需至少写入：
  - `id`
  - `content`
  - `fbtime`
  - `category`
  - `title`
  - `summary`
  - `pub_time`

### ChromaDB
路径：`/root/.hermes/chroma`
集合：`sx_essentials`

作用：
- 存储可检索的正文与 metadata
- 与 SQLite 保持数量和关键字段大体一致

## 日常检查命令

### 1. 查看 cron 状态
```bash
hermes cron list
```

重点检查：
- 是否存在两个任务：
  - `纷传圈子监控（UTC+8 12-19 每2小时）`
  - `纷传圈子监控（UTC+8 19-24 每30分钟）`
- `script` 是否都为 `sx_monitor.py`
- `schedule` 是否分别为：
  - `0 4,6,8,10 * * *`
  - `0,30 11-15 * * *`
- `last_status` 是否为 `ok`

### 2. 手动执行脚本
```bash
python3 /root/.hermes/scripts/sx_monitor.py
```

期望行为：
- 无新内容：无输出，退出码 0
- 有新内容：输出 `📰 [纷传圈子更新] ...`
- token/API 失效：输出 `⚠️ [纷传监控告警] ...`

### 3. 检查 SQLite
```bash
python3 - <<'PY'
import sqlite3
conn = sqlite3.connect('/root/sx_articles.db')
c = conn.cursor()
c.execute('SELECT COUNT(*) FROM articles')
print('count =', c.fetchone()[0])
c.execute('SELECT id, title, category, pub_time FROM articles ORDER BY id DESC LIMIT 5')
for row in c.fetchall():
    print(row)
PY
```

### 4. 检查 ChromaDB
```bash
python3 - <<'PY'
import chromadb
client = chromadb.PersistentClient(path='/root/.hermes/chroma')
col = client.get_or_create_collection(name='sx_essentials')
print('count =', col.count())
print(col.get(limit=3, offset=max(col.count()-3, 0), include=['metadatas']))
PY
```

## 故障判定

### 场景 A：cron 显示 ok，但用户一直收不到内容
先确认：
1. 当前是否真的没有新内容
2. 手动运行 `sx_monitor.py` 是否静默
3. cron 是否仍直接指向 `sx_monitor.py`
4. 是否有人误把任务改回 wrapper 或其他脚本
5. 两条分时段任务是否都仍然存在

### 场景 B：脚本报 token/API 错误
表现：
- 输出 `⚠️ [纷传监控告警] Token 已过期或失效！...`

处理：
- 更新 `/root/last_token.txt`
- 手动重跑一次脚本确认恢复

### 场景 C：SQLite / Chroma 数量明显不一致
处理顺序：
1. 先检查最近脚本运行是否有 Chroma 写入异常
2. 再检查 SQLite 是否成功写入新文章
3. 如涉及历史坏数据，按 `hermes-project-toolchain` 技能里的 `references/fenchuan-monitoring-repair.md` 执行修复

## 残留文件说明

- `/root/.hermes/scripts/sx_monitor_wrapper.py`

该文件目前**已删除**，不再被 cron 使用。
后续应继续保持“cron 直接调主脚本”的模式，避免重新引入配置漂移。
