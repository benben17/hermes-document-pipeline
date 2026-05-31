#!/usr/bin/env python3
"""
腾讯会议全自动机器人 v2
========================
功能：
  1. 自动识别消息中的腾讯会议链接/邀请码
  2. 按时间自动加入会议
  3. 每15分钟切割一个录音文件
  4. 每段录音自动 Whisper 转写 + AI 摘要
  5. 会议结束后整理全文 + 总结 + 详情
  6. 存入 ChromaDB 向量数据库 + D1
  7. 推送 Telegram

用法：
  # 完整流程
  python meeting_bot.py --url "https://meeting.tencent.com/dm/xxxx" --title "周例会" --at "2026-05-30 14:00"

  # 自动识别邀请码
  python meeting_bot.py --message "明天下午3点开会 腾讯会议号 123456789 密码 1234" --title "项目评审"

  # 立即加入
  python meeting_bot.py --code "123456789" --title "紧急会议"

  # 仅转写
  python meeting_bot.py --transcribe-only recordings/20260530_周例会_part01.wav --title "周例会"
"""

import requests
import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import threading
import logging
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# ── 配置 ────────────────────────────────────────────────────────────────
BASE_DIR         = Path("/opt/hermes/project/meeting")
RECORDS_DIR      = BASE_DIR / "recordings"
TRANSCRIPTS_DIR  = BASE_DIR / "transcripts"
SUMMARIES_DIR    = BASE_DIR / "summaries"
SCHEDULE_FILE    = BASE_DIR / "schedule.json"

DISPLAY_NUM      = ":99"
SCREEN_RES       = "1280x800x24"
PULSE_SINK       = "virtual_meeting"
WHISPER_MODEL    = "base"       # base / medium / large-v3
# CHUNK_MINUTES    = 15           # 录音切割间隔（分钟）
CHUNK_MINUTES    = 3            # 录音切割间隔（分钟） - 降低为 3 分钟以提高响应实时性
POLL_INTERVAL    = 10           # 会议状态轮询秒数
CHROMA_HOST      = "localhost"
CHROMA_PORT      = 8000
CHROMA_COLLECTION = "documents"

# 会议链接正则
RE_TENCENT_URL  = re.compile(r'(https?://meeting\.tencent\.com/dm/[\w-]+)', re.I)
RE_MEETING_CODE = re.compile(r'(?:会议号|会议码|邀请码|会议ID|Meeting\s*(?:ID|Code))[:\s：]*?(\d{3}[-\s]?\d{3,4}[-\s]?\d{3,4})', re.I)
RE_MEETING_PWD  = re.compile(r'(?:会议密码|密码|Passcode|Password)[:\s：]*?(\d{3,6})', re.I)
RE_MEETING_TIME = re.compile(
    r'(?:明天|今天|后天)?\s*(?:下午|上午|晚上|凌晨)?\s*(\d{1,2})[点时：:](\d{1,2})?\s*(?:开会|会议|开)?',
    re.I
)

for d in [RECORDS_DIR, TRANSCRIPTS_DIR, SUMMARIES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BASE_DIR / "meeting_bot.log"),
    ]
)
log = logging.getLogger("meeting_bot")

# ── 进程管理 ─────────────────────────────────────────────────────────────
procs = {}

def start_proc(name, cmd, env=None, **kwargs):
    e = {**os.environ, **(env or {})}
    p = subprocess.Popen(cmd, env=e, **kwargs)
    procs[name] = p
    log.info(f"[{name}] 启动 PID={p.pid}  cmd={' '.join(str(c) for c in cmd)}")
    return p

def kill_proc(name):
    p = procs.pop(name, None)
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
        log.info(f"[{name}] 已停止")

def cleanup(*_):
    log.info("清理所有子进程...")
    for name in list(procs.keys()):
        kill_proc(name)
    subprocess.run(["pactl", "unload-module", "module-null-sink"], capture_output=True)
    subprocess.run(["pactl", "unload-module", "module-virtual-source"], capture_output=True)
    log.info("清理完成")
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)


# ══════════════════════════════════════════════════════════════════════════
# 模块1: 消息解析 — 自动识别腾讯会议链接/邀请码/时间
# ══════════════════════════════════════════════════════════════════════════

def parse_meeting_message(text: str) -> dict:
    """
    从自然语言消息中提取会议信息：
    - meeting_url: 腾讯会议完整链接
    - meeting_code: 会议号（9-12位数字）
    - meeting_pwd:  会议密码
    - meeting_time: 预定时间（datetime 或 None）
    """
    result: dict[str, str | datetime | None] = {"meeting_url": None, "meeting_code": None, "meeting_pwd": None, "meeting_time": None}

    # 提取 URL
    m = RE_TENCENT_URL.search(text)
    if m:
        result["meeting_url"] = m.group(1)
        log.info(f"识别到会议链接: {result['meeting_url']}")

    # 提取会议号
    m = RE_MEETING_CODE.search(text)
    if m:
        code = re.sub(r'[-\s]', '', m.group(1))
        result["meeting_code"] = code
        log.info(f"识别到会议号: {code}")

    # 提取密码
    m = RE_MEETING_PWD.search(text)
    if m:
        result["meeting_pwd"] = m.group(1)
        log.info(f"识别到会议密码: {result['meeting_pwd']}")

    # 提取时间
    m = RE_MEETING_TIME.search(text)
    
    # 尝试提取标准日期时间格式 (例如 2026/05/31 10:56)
    m_std = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})\s+(\d{1,2}):(\d{1,2})', text)
    if m_std:
        year, month, day, hour, minute = map(int, m_std.groups())
        target_date = datetime(year, month, day, hour, minute)
        
        # 处理时区 (GMT+08:00) 转换为服务器本地时间
        # 假设服务器是 UTC，那么需要减去 8 小时。如果服务器已经是北京时间则不用。
        import time as _time
        is_server_utc = _time.timezone == 0
        if "GMT+08:00" in text or "北京" in text:
            if is_server_utc:
                target_date -= timedelta(hours=8)
                
        result["meeting_time"] = target_date
        log.info(f"识别到标准会议时间: {target_date.strftime('%Y-%m-%d %H:%M')} (服务器时区)")
    elif m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        # 上下文推断：下午/晚上 → +12
        context = text[:m.start()].lower()
        if '下午' in context or '晚上' in context or 'pm' in context:
            if hour < 12:
                hour += 12
        elif '凌晨' in context or '上午' in context or 'am' in context:
            pass  # 保持原值

        # 推断日期
        day_offset = 0
        if '后天' in text:
            day_offset = 2
        elif '明天' in text:
            day_offset = 1
            
        # 安全保护：如果识别出的时间已经过去超过半小时，默认它是明天的相同时间
        target_date = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        target_date += timedelta(days=day_offset)
        
        # 比如现在是下午 3点，说 "下午2点开会"，如果没有指定明天，这显然是个过去的日期，自动顺延到明天
        if day_offset == 0 and (datetime.now() - target_date).total_seconds() > 1800:
            target_date += timedelta(days=1)
            
        result["meeting_time"] = target_date
        log.info(f"识别到会议时间: {target_date.strftime('%Y-%m-%d %H:%M')}")

    # URL 不足时从会议号构造
    if not result["meeting_url"] and result["meeting_code"]:
        code = result["meeting_code"]
        # 腾讯会议号格式：xxx-xxx-xxx 或 xxxxxxxxx
        result["meeting_url"] = f"https://meeting.tencent.com/dm/{code}"

    return result


def save_schedule(meeting_info: dict, title: str):
    """保存会议到调度文件"""
    schedules = []
    if SCHEDULE_FILE.exists():
        try:
            schedules = json.loads(SCHEDULE_FILE.read_text())
        except Exception:
            schedules = []

    entry = {
        "id": hashlib.md5(f"{title}{datetime.now().isoformat()}".encode()).hexdigest()[:8],
        "title": title,
        "meeting_url": meeting_info["meeting_url"],
        "meeting_code": meeting_info["meeting_code"],
        "meeting_pwd": meeting_info["meeting_pwd"],
        "meeting_time": meeting_info["meeting_time"].isoformat() if meeting_info.get("meeting_time") else None,
        "status": "scheduled",
        "created_at": datetime.now().isoformat(),
    }
    schedules.append(entry)
    SCHEDULE_FILE.write_text(json.dumps(schedules, ensure_ascii=False, indent=2))
    log.info(f"会议已保存到调度: {entry['id']} — {title}")
    return entry


# ══════════════════════════════════════════════════════════════════════════
# 模块2: 虚拟环境 — Xvfb + PulseAudio
# ══════════════════════════════════════════════════════════════════════════

def start_xvfb():
    # 先杀残留
    subprocess.run(["pkill", "-f", f"Xvfb {DISPLAY_NUM}"], capture_output=True)
    time.sleep(0.5)
    log.info(f"启动 Xvfb {DISPLAY_NUM} {SCREEN_RES}")
    start_proc("xvfb", ["Xvfb", DISPLAY_NUM, "-screen", "0", SCREEN_RES, "-ac"])
    time.sleep(1.5)


def start_pulseaudio():
    log.info("启动 PulseAudio")
    env = {"DISPLAY": DISPLAY_NUM, "HOME": str(Path.home())}
    # 强制不作为系统模式启动，以 pulse 用户或 root 启动会有各种权限问题，这里使用 --system=false
    ret = subprocess.run(["pulseaudio", "--check"], capture_output=True)
    if ret.returncode != 0:
        start_proc("pulseaudio", [
            "pulseaudio", "--start", "--system=false", "--disallow-exit",
            "--log-target=file:" + str(BASE_DIR / "pulse.log")
        ], env=env)
        time.sleep(2)

    # 清理可能残留的模块
    subprocess.run(["pactl", "unload-module", "module-virtual-source"], capture_output=True)
    subprocess.run(["pactl", "unload-module", "module-null-sink"], capture_output=True)
    time.sleep(0.5)

    # 创建虚拟 null sink
    subprocess.run(["pactl", "unload-module", "module-null-sink"], capture_output=True)
    time.sleep(0.5)
    result = subprocess.run(
        ["pactl", "load-module", "module-null-sink",
         f"sink_name={PULSE_SINK}",
         f"sink_properties=device.description={PULSE_SINK}"],
        capture_output=True, text=True
    )
    log.info(f"虚拟 sink 模块 ID: {result.stdout.strip()}")

    # 创建虚拟 source
    subprocess.run(["pactl", "unload-module", "module-virtual-source"], capture_output=True)
    subprocess.run([
        "pactl", "load-module", "module-virtual-source",
        f"master={PULSE_SINK}.monitor",
        "source_name=virtual_mic",
        "source_properties=device.description=virtual_mic"
    ], capture_output=True)

    subprocess.run(["pactl", "set-default-sink", PULSE_SINK], capture_output=True)
    subprocess.run(["pactl", "set-default-source", "virtual_mic"], capture_output=True)
    
    # 获取所有的 sink/source，方便调试
    subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True)
    subprocess.run(["pactl", "list", "short", "sources"], capture_output=True)
    log.info("PulseAudio 虚拟声卡就绪")


# ══════════════════════════════════════════════════════════════════════════
# 模块3: 浏览器 RPA — 自动加入会议
# ══════════════════════════════════════════════════════════════════════════

def join_meeting_browser(meeting_url: str, display_name: str, meeting_pwd: str = None):
    """用 Playwright + Chromium 加入腾讯会议"""
    from playwright.sync_api import sync_playwright

    log.info(f"启动 Chromium 加入会议: {meeting_url}")
    os.environ["DISPLAY"] = DISPLAY_NUM
    # 强制将 Playwright 的音频输出设备指向我们的 PulseAudio
    os.environ["PULSE_SERVER"] = "unix:/tmp/pulse-socket" if Path("/tmp/pulse-socket").exists() else "127.0.0.1"

    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=False,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            "--autoplay-policy=no-user-gesture-required",
            f"--display={DISPLAY_NUM}",
            f"--alsa-output-device=pulse", # 强制 alsa 使用 pulse
        ]
    )
    context = browser.new_context(permissions=["microphone", "camera"])
    page = context.new_page()
    page.goto(meeting_url, timeout=30000)
    log.info("页面加载完成，等待会议界面...")

    # 尝试多个加入按钮选择器 (处理英文界面)
    join_selectors = [
        "text=加入会议",
        "text=立即加入",
        "text=加入",
        "text=Join Meeting",
        "text=Join",
        "button:has-text('加入')",
        "button:has-text('立即加入')",
        "button:has-text('Join')",
        "[class*='join']",
        "[class*='btn-join']",
        ".join-btn",
        ".meet-join-btn"
    ]
    joined = False
    for sel in join_selectors:
        try:
            # wait_for_selector 的状态加上 'visible'
            elem = page.wait_for_selector(sel, timeout=5000, state="visible")
            if elem:
                # 尝试用 evaluate 点击以避开覆盖层
                elem.evaluate("el => el.click()")
                log.info(f"点击加入按钮: {sel}")
                joined = True
                break
        except Exception as e:
            continue

    if not joined:
        log.warning("未找到加入按钮，可能已自动进入")

    try:
        # 英文/中文密码框支持
        pwd_input = page.wait_for_selector("input[type='password'], [placeholder*='密码'], [placeholder*='Passcode'], [placeholder*='Password']", timeout=5000)
        if pwd_input:
            pwd_input.fill(meeting_pwd)
            log.info(f"已输入会议密码")
            # 尝试确认
            for confirm_sel in ["text=确认", "text=加入", "text=Join", "text=Confirm", "button:has-text('确认')", "button:has-text('Join')"]:
                try:
                    page.click(confirm_sel)
                    break
                except Exception:
                    continue
    except Exception:
        log.info("未检测到密码输入框，可能不需要密码")

    try:
        # 在等待加入按钮之前，先判断是否提示“会议不存在”、“无效会议号”或者需要“登录”
        error_msg = page.query_selector("text=会议不存在, text=Invalid meeting ID")
        if error_msg:
            log.error("会议不存在或会议号无效")
            page.screenshot(path="/tmp/meeting_error.png")
            return None, None, None, None
            
        login_btn = page.query_selector("text=Log In, text=登录")
        if login_btn and not page.query_selector("[class*='join']"):
            log.warning("当前需要登录授权，尝试访客或跳过...")
            # 腾讯会议 web 版往往需要登录。这里可以提示用户配置 Cookie 或是使用免登录链接
    except Exception:
        pass

    # 处理一些常见的会议提示框 (如开启麦克风等)
    try:
        page.click("text=知道了", timeout=2000)
    except: pass
    try:
        page.click("text=确定", timeout=2000)
    except: pass
    try:
        page.click("button:has-text('Got it')", timeout=2000)
    except: pass

    # 输入参会名称
    try:
        name_input = page.wait_for_selector("input[placeholder*='名称'], input[placeholder*='名字'], input[placeholder*='Name']", timeout=3000)
        if name_input:
            name_input.fill(display_name)
            log.info(f"已输入参会名称: {display_name}")
    except Exception:
        pass

    # 等待进入会议室
    log.info("等待进入会议室...")
    try:
        # 如果是 web 页面，还需要处理提示“在浏览器中打开”的弹窗
        try:
            page.click("text=在浏览器中打开", timeout=3000)
            log.info("点击了'在浏览器中打开'")
        except Exception:
            try:
                page.click("text=Join from your browser", timeout=3000)
                log.info("点击了'Join from your browser'")
            except Exception:
                pass

        try:
            # 兼容英文 "Continue in browser"
            page.click("text=继续在浏览器中加入", timeout=3000)
            log.info("点击了'继续在浏览器中加入'")
        except Exception:
            try:
                page.click("text=Continue in browser", timeout=3000)
                log.info("点击了'Continue in browser'")
            except Exception:
                pass
            
        page.wait_for_selector("text=结束", timeout=30000)
        log.info("✅ 已成功加入会议室")
    except Exception:
        try:
            page.wait_for_selector("text=Leave", timeout=5000)
            log.info("✅ 已成功加入会议室 (Leave detected)")
        except Exception:
            log.warning("未检测到会议室界面，可能需要手动确认")

    return pw, browser, context, page


# ══════════════════════════════════════════════════════════════════════════
# 模块4: 录音 — 15分钟分段切割
# ══════════════════════════════════════════════════════════════════════════

class ChunkRecorder:
    """分段录音器：每 CHUNK_MINUTES 分钟切割一个 WAV 文件"""

    def __init__(self, base_name: str):
        self.base_name = base_name
        self.chunk_index = 0
        self.chunk_paths: list[Path] = []
        self._stop_event = threading.Event()
        self._thread = None

    def _current_path(self) -> Path:
        return RECORDS_DIR / f"{self.base_name}_part{self.chunk_index:02d}.wav"

    def _record_one_chunk(self, duration_sec: int) -> bool:
        """录制一个分段，返回 True 表示成功"""
        output = self._current_path()
        log.info(f"🎙️ 录音段 {self.chunk_index+1:02d} → {output} ({duration_sec}s)")
        
        # 使用 virtual_meeting.monitor 录音，如果失败了再 fallback
        cmd = [
            "ffmpeg", "-y",
            "-f", "pulse",
            "-i", f"{PULSE_SINK}.monitor",
            "-ar", "16000",
            "-ac", "1",
            "-acodec", "pcm_s16le",
            "-t", str(duration_sec),
            str(output)
        ]
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        procs[f"ffmpeg_chunk_{self.chunk_index}"] = p

        # 检查是否立刻报错退出了
        time.sleep(0.5)
        if p.poll() is not None:
            log.warning(f"{PULSE_SINK}.monitor 不存在，尝试使用 default")
            cmd = [
                "ffmpeg", "-y",
                "-f", "pulse",
                "-i", "default",
                "-ar", "16000",
                "-ac", "1",
                "-acodec", "pcm_s16le",
                "-t", str(duration_sec),
                str(output)
            ]
            p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            procs[f"ffmpeg_chunk_{self.chunk_index}"] = p
            
            time.sleep(0.5)
            if p.poll() is not None:
                log.warning("default 设备也不存在或 ffmpeg 依然失败，尝试使用 alsa hw:0")
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "alsa",
                    "-i", "default",
                    "-ar", "16000",
                    "-ac", "1",
                    "-acodec", "pcm_s16le",
                    "-t", str(duration_sec),
                    str(output)
                ]
                p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                procs[f"ffmpeg_chunk_{self.chunk_index}"] = p

        # 等待该段录制完成或收到停止信号
        while p.poll() is None:
            if self._stop_event.is_set():
                # 收到停止信号 → 发 SIGTERM 给 ffmpeg 让它正常 flush 并关闭文件
                p.terminate()
                p.wait(timeout=10)
                break
            time.sleep(1)

        # 检查文件
        if output.exists() and output.stat().st_size > 100:
            self.chunk_paths.append(output)
            log.info(f"✅ 录音段 {self.chunk_index+1:02d} 完成: {output.stat().st_size} bytes")
            return True
        else:
            log.warning(f"⚠️ 录音段 {self.chunk_index+1:02d} 异常，文件过小或不存在 (size: {output.stat().st_size if output.exists() else 0})")
            return False

    def start(self):
        """启动分段录音线程"""
        def _loop():
            while not self._stop_event.is_set():
                # 最后一段：如果收到停止信号，录剩余时间即可
                remaining = CHUNK_MINUTES * 60
                self._record_one_chunk(remaining)
                self.chunk_index += 1

        self._thread = threading.Thread(target=_loop, daemon=True, name="chunk-recorder")
        self._thread.start()
        log.info(f"分段录音器已启动，每 {CHUNK_MINUTES} 分钟一段")

    def stop(self) -> list[Path]:
        """停止录音，返回所有分段文件路径"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=30)
        log.info(f"录音停止，共 {len(self.chunk_paths)} 个分段")
        return self.chunk_paths


# ══════════════════════════════════════════════════════════════════════════
# 模块5: Whisper 转写
# ══════════════════════════════════════════════════════════════════════════

_whisper_model = None

def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        # 自动探测设备
        device = "cpu"
        compute_type = "int8"
        try:
            import torch
            if torch.cuda.is_available():
                device = "cuda"
                compute_type = "float16"
        except ImportError:
            pass
            
        log.info(f"加载 Whisper 模型: {WHISPER_MODEL} (Device: {device}, Compute: {compute_type})")
        _whisper_model = WhisperModel(WHISPER_MODEL, device=device, compute_type=compute_type)
    return _whisper_model


def transcribe(audio_path: Path, transcript_path: Path = None) -> str:
    log.info(f"Whisper 转写: {audio_path}")
    model = get_whisper_model()

    segments, info = model.transcribe(
        str(audio_path),
        language="zh",
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500}
    )

    lines = []
    for seg in segments:
        ts = f"[{seg.start:6.1f}s -> {seg.end:6.1f}s]"
        line = f"{ts} {seg.text.strip()}"
        lines.append(line)

    full_text = "\n".join(lines)

    if transcript_path:
        transcript_path.write_text(full_text, encoding="utf-8")
        log.info(f"转写完成，共 {len(lines)} 段 → {transcript_path}")

    return full_text


# ══════════════════════════════════════════════════════════════════════════
# 模块6: AI 摘要生成
# ══════════════════════════════════════════════════════════════════════════

def generate_chunk_summary(transcript: str, meeting_title: str, chunk_index: int) -> str:
    """生成单段摘要（实时处理，会议进行中即可看到）"""
    if not transcript or len(transcript.strip()) < 10:
        return "（未检测到有效语音发言）"
        
    log.info(f"生成第 {chunk_index+1} 段摘要...")
    prompt = f"""以下是会议「{meeting_title}」第 {chunk_index+1} 段（约{CHUNK_MINUTES}分钟）的逐字稿，请生成：

1. **本段摘要**（2-3条核心讨论点）
2. **发言要点**（谁说了什么，如有提及）
3. **待办事项**（Action Items，注明责任人）

逐字稿：
{transcript[:6000]}
"""
    # 尝试使用原生 Hermes API (CLI 封装) 避免子进程重度开销 (静默模式)
    try:
        result = subprocess.run(
            ["hermes", "chat", "-Q", "-q", prompt],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if lines and lines[0].startswith("session_id:"):
                return "\n".join(lines[1:]).strip()
            return result.stdout.strip()
    except Exception as e:
        log.warning(f"Hermes 接口调用失败 ({e})")
        
    # 兜底
    result = subprocess.run(
        ["hermes", "ask", prompt],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode == 0:
        return result.stdout.strip()
    else:
        log.warning(f"Hermes 段落摘要失败: {result.stderr}")
        return "（段落摘要生成失败）"


def generate_full_summary(all_transcripts: list[dict], meeting_title: str) -> dict:
    """
    会议结束后生成完整总结。
    输入: [{"chunk": 0, "transcript": "...", "summary": "..."}, ...]
    输出: {"summary": "总体摘要", "action_items": "待办", "decisions": "决策", "full_text": "全文"}
    """
    log.info("生成会议完整总结...")

    # 拼接所有段落的摘要
    chunk_summaries = "\n\n".join(
        f"### 第 {c['chunk']+1} 段\n{c['summary']}" for c in all_transcripts if c.get("summary")
    )
    # 拼接所有逐字稿
    full_transcript = "\n\n---\n\n".join(
        f"## 第 {c['chunk']+1} 段（约第 {c['chunk']*CHUNK_MINUTES}-{(c['chunk']+1)*CHUNK_MINUTES} 分钟）\n{c['transcript']}"
        for c in all_transcripts if c.get("transcript")
    )

    prompt = f"""以下是会议「{meeting_title}」的各段摘要和逐字稿，请生成完整会议纪要：

## 各段摘要：
{chunk_summaries[:8000]}

## 要求：
1. **会议总体摘要**（5-8条核心结论，最关键的信息放最前）
2. **Action Items 完整清单**（所有待办事项，注明责任人和截止日期）
3. **关键决策**（做了哪些正式决定）
4. **风险与待跟进**（悬而未决的问题、潜在风险）
5. **下次会议建议议题**（如有）

请用中文输出，格式清晰。
"""
    # 尝试使用原生 Hermes Client 避免子进程开销
    try:
        result = subprocess.run(
            ["hermes", "chat", "-Q", "-q", prompt],
            capture_output=True, text=True, timeout=180
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if lines and lines[0].startswith("session_id:"):
                full_summary = "\n".join(lines[1:]).strip()
            else:
                full_summary = result.stdout.strip()
        else:
            raise Exception(result.stderr)
    except Exception as e:
        log.warning(f"Hermes 原生接口调用失败 ({e})，降级使用 CLI...")
        result = subprocess.run(
            ["hermes", "ask", prompt],
            capture_output=True, text=True, timeout=180
        )
        full_summary = result.stdout.strip() if result.returncode == 0 else "（完整摘要生成失败）"

    return {
        "summary": full_summary,
        "full_text": full_transcript,
    }


# ══════════════════════════════════════════════════════════════════════════
# 模块7: ChromaDB 向量入库 + D1 元数据
# ══════════════════════════════════════════════════════════════════════════

def ingest_to_chroma(meeting_title: str, full_text: str, summary: str, meta: dict):
    """存入 ChromaDB 向量数据库"""
    log.info(f"ChromaDB 入库: {meeting_title}")
    try:
        import chromadb
        client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        col = client.get_or_create_collection(name=CHROMA_COLLECTION)

        doc_id = f"meeting_{meta.get('id', datetime.now().strftime('%Y%m%d_%H%M'))}"

        # 入库完整逐字稿（用于语义检索）
        col.upsert(
            ids=[doc_id],
            documents=[full_text],
            metadatas=[{
                "type": "meeting_transcript",
                "title": meeting_title,
                "date": meta.get("date", datetime.now().strftime("%Y-%m-%d")),
                "duration_min": str(meta.get("duration_min", "?")),
                "chunks": str(meta.get("chunks", 0)),
            }]
        )
        log.info(f"✅ 逐字稿已入库 ChromaDB: {doc_id}")

        # 入库摘要（也可检索）
        col.upsert(
            ids=[f"{doc_id}_summary"],
            documents=[summary],
            metadatas=[{
                "type": "meeting_summary",
                "title": meeting_title,
                "date": meta.get("date", datetime.now().strftime("%Y-%m-%d")),
            }]
        )
        log.info(f"✅ 摘要已入库 ChromaDB: {doc_id}_summary")

    except Exception as e:
        log.warning(f"ChromaDB 入库失败: {e}")


def ingest_to_d1(meeting_title: str, summary: str, meta: dict):
    """写入 D1 元数据（通过 project-tool doc）"""
    log.info(f"D1 入库: {meeting_title}")
    # 生成临时 md 文件，通过 doc_engine 入库
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    tmp_path = SUMMARIES_DIR / f"{ts}_{meeting_title}_full.md"
    tmp_path.write_text(
        f"# {meeting_title}\n\n"
        f"**日期：** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"**时长：** {meta.get('duration_min', '?')} 分钟\n"
        f"**录音段数：** {meta.get('chunks', 0)}\n\n"
        f"{summary}",
        encoding="utf-8"
    )
    try:
        ret = subprocess.run(
            ["project-tool", "process", str(tmp_path)],
            capture_output=True, text=True, timeout=60
        )
        log.info(f"D1 入库完成: {ret.stdout.strip()}")
    except Exception as e:
        log.warning(f"D1 入库失败: {e}")


# ══════════════════════════════════════════════════════════════════════════
# 模块8: Telegram 推送
# ══════════════════════════════════════════════════════════════════════════

def push_telegram(msg: str, media_path: str | None = None):
    """推送消息到 Telegram，可选附带媒体文件路径"""
    try:
        payload = msg
        if media_path:
            payload = f"{msg}\n\nMEDIA:{media_path}"
        result = subprocess.run(
            ["hermes", "send", "telegram", payload],
            capture_output=True, text=True, timeout=30
        )
        log.info("Telegram 推送完成")
    except Exception as e:
        log.warning(f"Telegram 推送失败: {e}")


def push_chunk_result(meeting_title: str, chunk_index: int, summary: str):
    """推送单段处理结果"""
    short = summary[:1500] if len(summary) > 1500 else summary
    msg = (
        f"🎙️ **{meeting_title}** — 第 {chunk_index+1} 段（{chunk_index*CHUNK_MINUTES}-{(chunk_index+1)*CHUNK_MINUTES}分钟）\n\n"
        f"{short}"
    )
    push_telegram(msg)


def push_final_result(meeting_title: str, summary: str, duration_min: int, chunks: int):
    """推送最终完整总结"""
    short = summary[:3500] if len(summary) > 3500 else summary
    msg = (
        f"📋 **会议完整纪要：{meeting_title}**\n"
        f"⏱ 时长：{duration_min} 分钟 | 🎙️ 录音段：{chunks}\n\n"
        f"{short}\n\n"
        f"✅ 已存入向量数据库，可随时查询"
    )
    push_telegram(msg)


# ══════════════════════════════════════════════════════════════════════════
# 模块9: 实时分段处理管线 — 每段录完立即转写+摘要+推送
# ══════════════════════════════════════════════════════════════════════════

def process_chunks_realtime(recorder: ChunkRecorder, meeting_title: str) -> list[dict]:
    """
    后台线程：监控录音分段，每段完成立即使用线程池 转写 → 摘要 → 推送
    返回所有段落的 transcript + summary 列表
    """
    all_results = []
    processed_count = 0
    futures = []
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        def _process_single_chunk(chunk_path: Path, chunk_idx: int):
            log.info(f"📥 开始处理第 {chunk_idx+1} 段: {chunk_path.name}")
            try:
                # 转写
                ts = datetime.now().strftime("%Y%m%d_%H%M")
                transcript_path = TRANSCRIPTS_DIR / f"{ts}_{meeting_title}_part{chunk_idx:02d}.txt"
                transcript = transcribe(chunk_path, transcript_path)

                # 摘要
                summary = generate_chunk_summary(transcript, meeting_title, chunk_idx)

                # 推送
                push_chunk_result(meeting_title, chunk_idx, summary)

                # 保存段落摘要
                chunk_summary_path = SUMMARIES_DIR / f"{ts}_{meeting_title}_part{chunk_idx:02d}_summary.md"
                chunk_summary_path.write_text(
                    f"# {meeting_title} — 第 {chunk_idx+1} 段\n\n{summary}\n\n---\n\n## 逐字稿\n\n```\n{transcript}\n```",
                    encoding="utf-8"
                )

                return {
                    "chunk": chunk_idx,
                    "transcript": transcript,
                    "summary": summary,
                    "audio_path": str(chunk_path),
                    "transcript_path": str(transcript_path),
                }

            except Exception as e:
                log.error(f"处理第 {chunk_idx+1} 段失败: {e}")
                return {
                    "chunk": chunk_idx,
                    "transcript": "",
                    "summary": f"（第 {chunk_idx+1} 段处理失败: {e}）",
                }

        while not recorder._stop_event.is_set() or processed_count < len(recorder.chunk_paths):
            # 检查是否有新完成的分段
            if processed_count < len(recorder.chunk_paths):
                chunk_path = recorder.chunk_paths[processed_count]
                chunk_idx = processed_count
                
                # 检查文件是否确实生成且不为空，如果文件大小为0或者极小，则跳过转写并认为无效
                if not chunk_path.exists() or chunk_path.stat().st_size < 100:
                    log.warning(f"录音段 {chunk_idx+1} 无效或极小，跳过转写")
                    all_results.append({
                        "chunk": chunk_idx,
                        "transcript": "",
                        "summary": "（未检测到有效语音发言）",
                        "audio_path": str(chunk_path) if chunk_path.exists() else "",
                        "transcript_path": "",
                    })
                    processed_count += 1
                    continue
                    
                # 提交到线程池
                future = executor.submit(_process_single_chunk, chunk_path, chunk_idx)
                futures.append(future)
                
                processed_count += 1
            else:
                time.sleep(3)
                
        # 收集结果
        for future in futures:
            all_results.append(future.result())

    # 按 chunk 排序以保证结果顺序
    all_results.sort(key=lambda x: x["chunk"])
    return all_results


# ══════════════════════════════════════════════════════════════════════════
# 模块10: 会议调度 — 定时自动参会
# ══════════════════════════════════════════════════════════════════════════

def wait_and_join(meeting_time: datetime, meeting_url: str, display_name: str,
                  meeting_pwd: str = None):
    """等待到预定时间前2分钟，然后启动环境并加入会议"""
    now = datetime.now()
    if meeting_time and meeting_time > now:
        wait_sec = (meeting_time - now).total_seconds() - 120  # 提前2分钟启动
        if wait_sec > 0:
            log.info(f"距离会议开始还有 {wait_sec/60:.1f} 分钟，等待中...")
            push_telegram(f"📅 会议已排程，将在 {meeting_time.strftime('%H:%M')} 自动加入")
            time.sleep(wait_sec)

    log.info("开始启动会议环境...")
    start_xvfb()
    start_pulseaudio()

    pw, browser, context, page = join_meeting_browser(meeting_url, display_name, meeting_pwd)
    return pw, browser, context, page


# ══════════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════════

def run_meeting(meeting_url: str, title: str, display_name: str,
                meeting_pwd: str = None, meeting_time: datetime = None,
                max_duration: int = 240):
    """完整会议流程"""
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    base_name = f"{ts}_{title}"
    start_time = datetime.now()

    push_telegram(f"🚀 即将加入会议: **{title}**\n🔗 {meeting_url}")

    # 0. 若未携带 pw/browser 上下文，走"定时发码"分支：发送登录二维码，等待用户扫码
    #    该分支仅在 headless 下执行，不依赖 Xvfb/桌面
    qr_login_ok = False
    try:
        from playwright.sync_api import sync_playwright
        import time as _time
        _p = sync_playwright().start()
        _b = _p.chromium.launch(headless=True, args=["--no-sandbox","--disable-setuid-sandbox","--disable-gpu"])
        _ctx = _b.new_context(locale="zh-CN", viewport={"width":1280,"height":900})
        _page = _ctx.new_page()
        _page.goto("https://meeting.tencent.com", timeout=45000)
        _page.wait_for_load_state("domcontentloaded")
        # 关闭弹窗
        try:
            for _sel in ['[class*="modal"] [class*="close"]','svg[aria-label="close"]','button:has-text("领取")']:
                try:
                    _el = _page.locator(_sel).first
                    if _el.is_visible(timeout=500):
                        _el.click(); _time.sleep(0.5)
                        break
                except Exception:
                    pass
            _page.evaluate("() => { document.querySelectorAll('.mt-model, [class*=\"modal\"], [class*=\"popup\"]').forEach(e=>e.remove()); }")
        except Exception:
            pass
        # 点登录
        try:
            _page.get_by_text('登录', exact=False).first.click()
            _page.wait_for_load_state("domcontentloaded")
            _time.sleep(3)
        except Exception as e:
            log.warning(f"登录页加载失败: {e}")
        # 截图二维码
        _qr_path = f"/opt/hermes/project/meeting/recordings/we/qr_{int(_time.time())}.png"
        try:
            _img = _page.locator('img[src*="qr"], img[class*="qr"]').first
            _img.screenshot(path=_qr_path)
        except Exception:
            _page.screenshot(path=_qr_path)
        push_telegram(
            f"📱 请使用微信/腾讯会议 App 扫码登录（5分钟内有效）\n"
            f"🔗 {meeting_url}\n"
            f"⏰ 随后将自动进入会议：{title}",
            media_path=_qr_path,
        )
        # 轮询登录状态
        _start = _time.time()
        while _time.time() - _start < 300:
            try:
                _url = _page.url
                if "/dm/" in _url or _page.locator('text=加入会议, text=Join Meeting, text=正在进入').count() > 0:
                    qr_login_ok = True
                    break
            except Exception:
                pass
            _time.sleep(3)
        _b.close(); _p.stop()
    except Exception as e:
        log.warning(f"二维码登录流程异常: {e}")

    if qr_login_ok:
        push_telegram(f"✅ 登录成功，正在进入会议：{title}")
        join_result = None
        pw = browser = context = None
        # TODO：后续版本在此复用已登录上下文，直接导航到会议页并返回 page
        page = None
    else:
        # 1. 启动环境 + 加入会议
        join_result = wait_and_join(
            meeting_time, meeting_url, display_name, meeting_pwd
        )
    if join_result is None or join_result[0] is None:
        log.error("加入会议失败")
        push_telegram(f"❌ 加入会议失败: **{title}**\n原因: 会议号无效或需要登录授权")
        cleanup()
        return
    log.info("加入会议成功，继续后续流程...")
    pw, browser, context, page = join_result

    # 2. 启动分段录音
    recorder = ChunkRecorder(base_name)
    recorder.start()
    time.sleep(2)

    # 3. 启动实时处理线程
    processing_results = []
    def _process_thread():
        nonlocal processing_results
        processing_results = process_chunks_realtime(recorder, title)

    proc_thread = threading.Thread(target=_process_thread, daemon=True, name="chunk-processor")
    proc_thread.start()

    # 4. 监控会议状态
    log.info(f"会议进行中，最长 {max_duration} 分钟...")
    deadline = time.time() + max_duration * 60

    while time.time() < deadline:
        try:
            if page.is_closed():
                log.info("浏览器页面关闭，会议已结束")
                break
            # 检测会议结束提示
            try:
                ended = page.query_selector("text=会议已结束")
                if ended:
                    log.info("检测到「会议已结束」提示")
                    break
                ended_en = page.query_selector("text=Meeting has ended")
                if ended_en:
                    log.info("检测到「Meeting has ended」提示")
                    break
            except Exception:
                pass
        except Exception:
            break
        time.sleep(POLL_INTERVAL)

    # 5. 会议结束
    log.info("会议结束，开始收尾...")
    end_time = datetime.now()
    duration_min = int((end_time - start_time).total_seconds() / 60)

    # 停止录音
    recorder.stop()
    time.sleep(2)

    # 关闭浏览器
    try:
        browser.close()
    except Exception:
        pass
    try:
        pw.stop()
    except Exception:
        pass

    # 等待处理线程完成最后一段
    proc_thread.join(timeout=300)

    # 6. 生成完整总结
    if processing_results:
        full_result = generate_full_summary(processing_results, title)

        # 7. 保存完整文件
        full_path = SUMMARIES_DIR / f"{base_name}_full.md"
        full_path.write_text(
            f"# {title} — 完整会议纪要\n\n"
            f"**日期：** {start_time.strftime('%Y-%m-%d %H:%M')} → {end_time.strftime('%H:%M')}\n"
            f"**时长：** {duration_min} 分钟\n"
            f"**录音段数：** {len(recorder.chunk_paths)}\n\n"
            f"---\n\n"
            f"{full_result['summary']}\n\n"
            f"---\n\n"
            f"## 完整逐字稿\n\n{full_result['full_text']}",
            encoding="utf-8"
        )
        log.info(f"完整纪要已保存: {full_path}")

        # 8. ChromaDB + D1 入库
        meta = {
            "id": hashlib.md5(f"{title}{start_time.isoformat()}".encode()).hexdigest()[:8],
            "date": start_time.strftime("%Y-%m-%d"),
            "duration_min": duration_min,
            "chunks": len(recorder.chunk_paths),
        }
        ingest_to_chroma(title, full_result["full_text"], full_result["summary"], meta)
        ingest_to_d1(title, full_result["summary"], meta)

        # 9. 推送最终总结
        push_final_result(title, full_result["summary"], duration_min, len(recorder.chunk_paths))
    else:
        log.warning("没有可处理的录音数据")
        push_telegram(f"⚠️ 会议「{title}」结束，但未录到有效音频")

    # 清理
    cleanup()


def main():
    parser = argparse.ArgumentParser(description="腾讯会议自动参会+录音+转写+摘要 全流程")
    parser.add_argument("--url", help="会议链接（完整URL）")
    parser.add_argument("--code", help="会议号（9-12位数字）")
    parser.add_argument("--pwd", help="会议密码")
    parser.add_argument("--message", help="自然语言消息，自动提取会议链接/号/密码/时间")
    parser.add_argument("--name", default="记录助手", help="入会名称")
    parser.add_argument("--title", default="会议", help="会议标题")
    parser.add_argument("--at", help="预定时间，格式: '2026-05-30 14:00'")
    parser.add_argument("--duration", type=int, default=240, help="最长会议时长（分钟），默认240")
    parser.add_argument("--transcribe-only", help="仅转写已有录音文件/目录")
    parser.add_argument("--schedule", action="store_true", help="仅排程，不立即加入")
    args = parser.parse_args()

    # ── 仅转写模式 ──
    if args.transcribe_only:
        path = Path(args.transcribe_only)
        if path.is_dir():
            # 批量转写目录下所有 wav
            wav_files = sorted(path.glob("*.wav"))
            log.info(f"批量转写 {len(wav_files)} 个文件...")
            all_results = []
            for wf in wav_files:
                ts = datetime.now().strftime("%Y%m%d_%H%M")
                tp = TRANSCRIPTS_DIR / f"{ts}_{args.title}_{wf.stem}.txt"
                text = transcribe(wf, tp)
                summary = generate_chunk_summary(text, args.title, 0)
                all_results.append({"chunk": 0, "transcript": text, "summary": summary})
            if all_results:
                full = generate_full_summary(all_results, args.title)
                meta = {"date": datetime.now().strftime("%Y-%m-%d"), "duration_min": "?", "chunks": len(wav_files)}
                ingest_to_chroma(args.title, full["full_text"], full["summary"], meta)
                push_final_result(args.title, full["summary"], 0, len(wav_files))
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M")
            tp = TRANSCRIPTS_DIR / f"{ts}_{args.title}.txt"
            text = transcribe(path, tp)
            summary = generate_chunk_summary(text, args.title, 0)
            push_chunk_result(args.title, 0, summary)
        return

    # ── 解析会议信息 ──
    meeting_info = {"meeting_url": None, "meeting_code": None, "meeting_pwd": None, "meeting_time": None}

    if args.message:
        meeting_info = parse_meeting_message(args.message)
        log.info(f"消息解析结果: {meeting_info}")

    if args.url:
        meeting_info["meeting_url"] = args.url
    if args.code:
        meeting_info["meeting_code"] = re.sub(r'[-\s]', '', args.code)
        if not meeting_info["meeting_url"]:
            meeting_info["meeting_url"] = f"https://meeting.tencent.com/dm/{meeting_info['meeting_code']}"
    if args.pwd:
        meeting_info["meeting_pwd"] = args.pwd
    if args.at:
        meeting_info["meeting_time"] = datetime.strptime(args.at, "%Y-%m-%d %H:%M")

    if not meeting_info["meeting_url"] and not meeting_info["meeting_code"]:
        log.error("❌ 未识别到会议链接或会议号，请提供 --url / --code / --message")
        sys.exit(1)

    # 确保URL存在
    if not meeting_info["meeting_url"]:
        meeting_info["meeting_url"] = f"https://meeting.tencent.com/dm/{meeting_info['meeting_code']}"

    # ── 仅排程模式 ──
    if args.schedule:
        entry = save_schedule(meeting_info, args.title)
        push_telegram(
            f"📅 会议已排程: **{args.title}**\n"
            f"🔗 {meeting_info['meeting_url']}\n"
            f"⏰ {meeting_info['meeting_time'].strftime('%Y-%m-%d %H:%M') if meeting_info['meeting_time'] else '待定'}\n"
            f"🆔 {entry['id']}"
        )
        log.info(f"排程完成: {entry['id']}")
        return

    # ── 完整流程 ──
    run_meeting(
        meeting_url=meeting_info["meeting_url"],
        title=args.title,
        display_name=args.name,
        meeting_pwd=meeting_info["meeting_pwd"],
        meeting_time=meeting_info["meeting_time"],
        max_duration=args.duration,
    )


if __name__ == "__main__":
    main()
