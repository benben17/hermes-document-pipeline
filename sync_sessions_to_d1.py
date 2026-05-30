#!/usr/bin/env python3
"""
sync_sessions_to_d1.py
增量同步本地 Hermes session SQLite → Cloudflare D1 (Finance 库)

用法:
    python3 sync_sessions_to_d1.py           # 增量同步（默认）
    python3 sync_sessions_to_d1.py --full    # 强制全量重同步
    python3 sync_sessions_to_d1.py --stats   # 只看统计

D1 表:
    sessions    — 会话元数据（95条）
    messages    — user/assistant 消息（~3000条）
    sync_state  — 同步进度游标

策略:
    - 多行 INSERT (VALUES (...),(...)) 批量写，减少 API 请求次数
    - sessions  INSERT OR REPLACE（幂等）
    - messages  INSERT OR IGNORE（id 唯一，避免重复）
    - content 截断 6000 字符，防止单批 payload 超限
    - 每批 20 行提交一次，更新 sync_state 游标
"""

import os, sys, re, json, sqlite3, time
import urllib.request, urllib.error
from pathlib import Path

# ── 配置 ────────────────────────────────────────────────────────────────────
STATE_DB   = Path("/root/.hermes/state.db")
ENV_FILE   = Path("/root/.hermes/.env")
ACCOUNT_ID = "7c9dd6ac6350c8519eb55ba2c7ae34f8"
DB_ID      = "4a46994d-cbbc-4714-98fa-befc912fb13a"
BATCH_ROWS = 5        # D1 SQL变量上限约999，sessions 14列×5行=70，messages 8列×5行=40
CONTENT_MAX = 6000    # 消息内容最大字符数

# ── 凭证 ─────────────────────────────────────────────────────────────────────
def load_token() -> str:
    prefix = "CLOUDFLARE_API_TOKEN"
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith(prefix) and "=" in line:
            return line.split("=", 1)[1].strip()
    raise RuntimeError("CLOUDFLARE_API_TOKEN not found in .env")

TOKEN: str = ""  # 延迟初始化

# ── D1 单请求执行 ─────────────────────────────────────────────────────────────
def d1_exec(sql: str, params: list | None = None) -> dict:
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/d1/database/{DB_ID}/query"
    body: dict = {"sql": sql}
    if params:
        body["params"] = params
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        raise RuntimeError(f"D1 HTTP {e.code}: {body[:400]} | sql={sql[:120]}")
    if not data.get("success"):
        raise RuntimeError(f"D1 error: {data.get('errors')} | sql={sql[:120]}")
    return data["result"][0]

# ── 多行 INSERT 批量写 ────────────────────────────────────────────────────────
def d1_multi_insert(table: str, columns: list[str], rows_params: list[list],
                    conflict: str = "OR IGNORE") -> int:
    """
    用单条多值 INSERT 写入 rows_params（每次最多 BATCH_ROWS 行）。
    返回实际写入行数。
    """
    if not rows_params:
        return 0
    written = 0
    for i in range(0, len(rows_params), BATCH_ROWS):
        chunk = rows_params[i:i + BATCH_ROWS]
        ph = "(" + ",".join("?" * len(columns)) + ")"
        placeholders = ",".join([ph] * len(chunk))
        sql = f"INSERT {conflict} INTO {table} ({','.join(columns)}) VALUES {placeholders}"
        flat_params = [v for row in chunk for v in row]
        d1_exec(sql, flat_params)
        written += len(chunk)
    return written

# ── 同步进度 ─────────────────────────────────────────────────────────────────
def get_cursor() -> int:
    r = d1_exec("SELECT value FROM sync_state WHERE key='last_message_id'")
    rows = r.get("results", [])
    return int(rows[0]["value"]) if rows else 0

def set_cursor(msg_id: int):
    d1_exec(
        "INSERT OR REPLACE INTO sync_state(key,value) VALUES('last_message_id',?)",
        [str(msg_id)]
    )

# ── 同步 sessions ─────────────────────────────────────────────────────────────
def sync_sessions(conn: sqlite3.Connection):
    print("→ 同步 sessions …")
    cur = conn.cursor()
    cols_local = ["id","source","user_id","model","title",
                  "started_at","ended_at","end_reason",
                  "message_count","tool_call_count",
                  "input_tokens","output_tokens",
                  "estimated_cost_usd","billing_provider"]
    cur.execute(f"SELECT {','.join(cols_local)} FROM sessions")
    rows = cur.fetchall()

    params_list = [list(r) for r in rows]
    written = d1_multi_insert("sessions", cols_local, params_list, conflict="OR REPLACE")
    print(f"✓ sessions：{written}/{len(rows)} 条同步完成")

# ── 同步 messages ─────────────────────────────────────────────────────────────
def sync_messages(conn: sqlite3.Connection, force_full: bool = False):
    last_id = 0 if force_full else get_cursor()
    print(f"→ 同步 messages（上次游标 id={last_id}）…")

    cur = conn.cursor()
    cur.execute("""
        SELECT id, session_id, role, content, tool_name,
               timestamp, token_count, platform_message_id
        FROM messages
        WHERE id > ? AND role IN ('user','assistant')
        ORDER BY id ASC
    """, (last_id,))
    rows = cur.fetchall()
    total = len(rows)
    print(f"  待同步 {total} 条消息")

    if not rows:
        print("✓ 无新消息，跳过")
        return

    cols_d1 = ["id","session_id","role","content","tool_name",
               "timestamp","token_count","platform_message_id"]

    # 分批写，每批写完更新游标
    synced = 0
    for i in range(0, total, BATCH_ROWS):
        chunk_rows = rows[i:i + BATCH_ROWS]
        params_list = []
        for r in chunk_rows:
            rd = dict(zip(cols_d1, r))
            rd["content"] = (rd["content"] or "")[:CONTENT_MAX]
            params_list.append([rd[c] for c in cols_d1])

        d1_multi_insert("messages", cols_d1, params_list, conflict="OR IGNORE")
        synced += len(chunk_rows)
        batch_max_id = chunk_rows[-1][0]  # id 是第0列
        set_cursor(batch_max_id)
        print(f"  messages {synced}/{total}  cursor={batch_max_id}")

    print(f"✓ messages 同步完成：{total} 条")

# ── 统计 ──────────────────────────────────────────────────────────────────────
def print_stats():
    sc = d1_exec("SELECT COUNT(*) AS c FROM sessions")["results"][0]["c"]
    mc = d1_exec("SELECT COUNT(*) AS c FROM messages")["results"][0]["c"]
    cursor = get_cursor()
    print(f"D1 sessions={sc}  messages={mc}  last_cursor={cursor}")

# ── 主入口 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    TOKEN = load_token()
    force_full = "--full" in sys.argv
    stats_only = "--stats" in sys.argv

    if stats_only:
        print_stats()
        sys.exit(0)

    t0 = time.time()
    conn = sqlite3.connect(str(STATE_DB))
    try:
        sync_sessions(conn)
        sync_messages(conn, force_full)
    finally:
        conn.close()

    elapsed = time.time() - t0
    print(f"\n✅ 全部同步完成，耗时 {elapsed:.1f}s")
    print_stats()
