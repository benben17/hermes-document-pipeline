#!/usr/bin/env python3
"""
腾讯会议自动调度器 (Cron 守护进程)
========================
此脚本被设计为通过 crontab 每分钟运行一次 (* * * * *)。
作用：
1. 扫描 schedule.json 中的排期数据
2. 发现未来 2 分钟内即将开始的会议
3. 自动在后台拉起 `start_meeting.sh`
4. 防止漏会、断电重启错失等问题
"""

import json
import os
import subprocess
import logging
from datetime import datetime, timedelta
from pathlib import Path

# ── 配置 ────────────────────────────────────────────────────────────────
BASE_DIR      = Path("/opt/hermes/project/meeting")
SCHEDULE_FILE = BASE_DIR / "schedule.json"
LOG_FILE      = BASE_DIR / "scheduler.log"
START_SCRIPT  = BASE_DIR / "start_meeting.sh"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE),
    ]
)
log = logging.getLogger("scheduler")

def main():
    if not SCHEDULE_FILE.exists():
        return

    try:
        with open(SCHEDULE_FILE, 'r', encoding='utf-8') as f:
            schedules = json.load(f)
    except Exception as e:
        log.error(f"读取 schedule.json 失败: {e}")
        return

    now = datetime.now()
    dirty = False

    for entry in schedules:
        status = entry.get("status", "")
        if status != "scheduled":
            continue
        
        m_time_str = entry.get("meeting_time")
        if not m_time_str:
            # 如果没有时间，默认为立刻执行
            time_diff = 0 
        else:
            try:
                m_time = datetime.fromisoformat(m_time_str)
                time_diff = (m_time - now).total_seconds()
            except Exception:
                continue

        # 核心逻辑：提前 2 分钟启动。如果因为宕机迟到了，只要迟到在 15 分钟内，依然强行拉起
        if -900 <= time_diff <= 120:
            log.info(f"🚀 触发会议拉起: {entry['title']} (计划时间: {m_time_str})")
            
            # 准备启动命令
            cmd = [str(START_SCRIPT)]
            if entry.get("meeting_url"):
                cmd.extend(["--url", entry["meeting_url"]])
            elif entry.get("meeting_code"):
                cmd.extend(["--code", entry["meeting_code"]])
                
            if entry.get("meeting_pwd"):
                cmd.extend(["--pwd", entry["meeting_pwd"]])
                
            cmd.extend(["--title", entry.get("title", "未命名会议")])
            
            # 使用 Popen 的 start_new_session=True 完全脱离父进程（守护态）
            try:
                subprocess.Popen(
                    cmd,
                    cwd=str(BASE_DIR),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                log.info(f"✅ 已在后台成功拉起进程: {' '.join(cmd)}")
                entry["status"] = "running"
                entry["launched_at"] = now.isoformat()
                dirty = True
            except Exception as e:
                log.error(f"❌ 启动会议进程失败: {e}")
        
        # 清理逻辑：如果会议错过了2小时以上（比如停机半天），直接标记为 expired 放弃
        elif time_diff < -7200:
            log.info(f"🗑️ 清理严重过期会议: {entry['title']}")
            entry["status"] = "expired"
            dirty = True

    # 有状态变更才回写文件
    if dirty:
        try:
            with open(SCHEDULE_FILE, 'w', encoding='utf-8') as f:
                json.dump(schedules, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"回写 schedule.json 失败: {e}")

if __name__ == "__main__":
    main()