from datetime import datetime
from pathlib import Path
from collections import deque
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time

from flask import Flask, g, jsonify, render_template, request, send_file
from PIL import Image, UnidentifiedImageError

from api_clients import (
    analyze_images_with_qwen_vl,
    generate_image_with_local_model,
    generate_image_with_openai,
    generate_prompt_markdown_with_api,
    search_destination_web_context,
    write_city_script_with_deepseek,
    write_script_with_deepseek,
)


BASE_DIR = Path(__file__).parent
IMAGE_DIR = BASE_DIR / "images"
AUDIO_DIR = BASE_DIR / "audio"
OUTPUT_DIR = BASE_DIR / "output"
EXPORTS_DIR = BASE_DIR / "exports"
HISTORY_DIR = BASE_DIR / "history"
HISTORY_VIDEO_DIR = HISTORY_DIR / "videos"
HISTORY_FILE = HISTORY_DIR / "history.json"
SCRIPT_FILE = BASE_DIR / "script.json"
PROMPTS_FILE = BASE_DIR / "IMAGE_PROMPTS.md"
OUTPUT_FILE = OUTPUT_DIR / "final.mp4"
PROGRESS_FILE = OUTPUT_DIR / "progress.json"
LOG_DIR = BASE_DIR / "generated_cache" / "logs"
SYSTEM_LOG_FILE = LOG_DIR / "system.log"
REFERENCE_IMAGE_DIR = BASE_DIR / "generated_cache" / "reference_images"

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
ALLOWED_IMAGE_FORMAT_LABEL = "jpg/jpeg/png/webp/bmp/tif/tiff"
MAX_CONTENT_LENGTH = 80 * 1024 * 1024
MAX_UPLOAD_FILES = 120
MAX_IMAGE_BATCH = 12
MAX_DASHBOARD_ITEMS = 80
DEFAULT_STYLE_KEYWORDS = "真实摄影、电影感、纪录片风格、自然光、高清、细节丰富、色彩自然、干净画面、旅行短视频、统一色调、画面连贯"
DEFAULT_NEGATIVE_KEYWORDS = "避免文字、避免水印、避免Logo、避免插画风、避免卡通感、避免AI感、避免乱码招牌、避免错误建筑结构、避免不自然建筑比例、避免近景清晰人脸、避免夸张肢体、避免过度滤镜"
DEFAULT_SCENES = ["城市清晨远景", "老街巷入口", "地标建筑外观", "地方建筑细节", "街头生活氛围", "本地文化元素", "游客背影慢行", "傍晚收尾远景"]
SENSITIVE_KEYWORDS = {
    "违法犯罪": ["毒品", "枪支", "爆炸物", "诈骗", "洗钱", "赌博", "盗窃", "抢劫", "黑产"],
    "暴力血腥": ["血腥", "虐杀", "自杀", "杀人", "恐怖袭击"],
    "色情低俗": ["色情", "裸露", "成人视频", "性交易", "约炮"],
    "仇恨歧视": ["种族歧视", "仇恨", "纳粹"],
    "隐私侵权": ["身份证号", "银行卡号", "手机号泄露", "偷拍"],
}

app = Flask(__name__)
app.secret_key = "ai-travel-pseudo-video"
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

generation_lock = threading.Lock()
generation_thread = None
api_call_events = deque(maxlen=MAX_DASHBOARD_ITEMS)
compliance_events = deque(maxlen=MAX_DASHBOARD_ITEMS)
dashboard_lock = threading.Lock()


def ensure_history_dirs():
    HISTORY_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    if not HISTORY_FILE.exists():
        HISTORY_FILE.write_text("[]", encoding="utf-8")


def ensure_dirs():
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REFERENCE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ensure_history_dirs()


def append_system_log(level, message, data=None):
    ensure_dirs()
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "message": message,
        "data": data or {},
    }
    line = json.dumps(entry, ensure_ascii=False)
    try:
        with SYSTEM_LOG_FILE.open("a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")
    except OSError:
        pass


def read_system_logs(limit=80):
    if not SYSTEM_LOG_FILE.exists():
        return []
    try:
        lines = SYSTEM_LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    except OSError:
        return []
    logs = []
    for line in lines:
        try:
            logs.append(json.loads(line))
        except json.JSONDecodeError:
            logs.append({"time": "", "level": "INFO", "message": line, "data": {}})
    return logs


def add_api_call_event(method, path, status_code, duration_ms, ok=True):
    event = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "method": method,
        "path": path,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "ok": bool(ok),
    }
    with dashboard_lock:
        api_call_events.appendleft(event)
    if path.startswith("/api/") and path != "/api/backend_dashboard":
        append_system_log("INFO" if ok else "WARN", "API 调用", event)


def scan_content(text):
    value = str(text or "")
    findings = []
    for category, keywords in SENSITIVE_KEYWORDS.items():
        matched = [keyword for keyword in keywords if keyword in value]
        if matched:
            findings.append({"category": category, "keywords": matched[:5]})
    return {"ok": not findings, "findings": findings}


def record_compliance_event(source, status, message, details=None):
    event = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "source": source,
        "status": status,
        "message": message,
        "details": details or {},
    }
    with dashboard_lock:
        compliance_events.appendleft(event)
    level = "INFO" if status == "通过" else "WARN"
    append_system_log(level, f"合规检查：{source} {status}", event)
    return event


def require_clean_text(source, fields):
    merged = "\n".join(str(value or "") for value in fields.values())
    result = scan_content(merged)
    if result["ok"]:
        record_compliance_event(source, "通过", "未发现敏感词命中。", {"fields": list(fields.keys())})
        return None
    record_compliance_event(source, "拦截", "内容命中基础合规规则，已阻止继续处理。", result)
    return api_error("内容合规检查未通过，请调整输入后再试。", {"compliance": result}, status=400)


@app.before_request
def before_request():
    g.request_start_time = time.perf_counter()


@app.after_request
def after_request(response):
    start_time = getattr(g, "request_start_time", None)
    duration_ms = int((time.perf_counter() - start_time) * 1000) if start_time else 0
    if request.path.startswith("/api/"):
        add_api_call_event(request.method, request.path, response.status_code, duration_ms, response.status_code < 400)
    return response


def api_ok(message, data=None):
    return jsonify({"ok": True, "message": message, "data": data or {}})


def api_error(message, data=None, status=400):
    return jsonify({"ok": False, "message": message, "data": data or {}}), status


def write_progress(percent, message, done=False, error=""):
    ensure_dirs()
    payload = {"percent": int(max(0, min(100, percent))), "message": message, "done": bool(done), "error": error or ""}
    temp_path = PROGRESS_FILE.with_suffix(PROGRESS_FILE.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(PROGRESS_FILE)
    append_system_log("ERROR" if error else "INFO", f"视频生成进度：{payload['percent']}% {message}", {"done": done, "error": error})


def read_progress():
    if not PROGRESS_FILE.exists():
        return {"percent": 0, "message": "暂无生成任务", "done": False, "error": ""}
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"percent": 0, "message": "正在读取生成进度...", "done": False, "error": ""}
    return {
        "percent": int(max(0, min(100, data.get("percent", 0)))),
        "message": data.get("message") or "",
        "done": bool(data.get("done", False)),
        "error": data.get("error") or "",
        "output_file": output_file_info_payload(),
    }


def get_image_files():
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    def sort_key(path):
        return (0, int(path.stem)) if path.stem.isdigit() else (1, path.name.lower())

    return [p.name for p in sorted((p for p in IMAGE_DIR.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE_EXTS), key=sort_key)]


def get_image_count():
    return len(get_image_files())


def load_script():
    if SCRIPT_FILE.exists():
        try:
            data = json.loads(SCRIPT_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"title": "", "duration_per_image": 6, "aspect_ratio": "16:9", "subtitles": [], "voiceover": "", "tone_label": "治愈文艺"}


def save_script(data):
    SCRIPT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_prompt_output():
    return PROMPTS_FILE.read_text(encoding="utf-8") if PROMPTS_FILE.exists() else ""


def prompt_scene_titles(markdown=None):
    text = markdown if markdown is not None else read_prompt_output()
    titles = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("## "):
            continue
        match = re.match(r"^##\s+(\d{1,2})\.(?:jpg|jpeg|png)\s+(.+)$", line, re.I)
        if not match:
            continue
        title = match.group(2).strip()
        title = re.sub(r"^镜头标题[:：]\s*", "", title)
        title = re.sub(r"^[：:]\s*", "", title)
        if title:
            titles.append(title)
    return titles


def script_from_prompt_titles(destination, image_count, duration, tone_label):
    titles = prompt_scene_titles()[:image_count]
    if len(titles) < image_count:
        titles.extend([f"{destination}旅行画面 {index}" for index in range(len(titles) + 1, image_count + 1)])

    subtitles = []
    voiceover = []
    for index, title in enumerate(titles[:image_count], start=1):
        clean = title.strip("。")
        subtitles.append(clean if len(clean) <= 18 else clean[:18])
        voiceover.append(f"第{index}幕，画面来到{clean}，让这段旅程顺着眼前的风景继续向前。")

    return {
        "title": f"{destination}慢旅行" if destination else "旅行慢视频",
        "subtitles": subtitles,
        "voiceover": voiceover,
        "xiaohongshu_title": f"{destination}慢旅行｜跟着画面走进风景" if destination else "旅行慢视频｜跟着画面走进风景",
        "video_description": "字幕和旁白已按当前图片提示词顺序生成，确保每一幕尽量对应同序号画面。",
        "image_order_advice": "当前文案按 images 文件夹文件名顺序和 IMAGE_PROMPTS.md 镜头标题生成。",
        "tone_label": tone_label,
    }


def default_prompt_form():
    return {
        "destination": "",
        "aspect_ratio": "16:9",
        "style_keywords": DEFAULT_STYLE_KEYWORDS,
        "negative_keywords": DEFAULT_NEGATIVE_KEYWORDS,
        "scenes_text": "\n".join(DEFAULT_SCENES),
    }


def get_video_size_mb(path):
    return f"{Path(path).stat().st_size / 1024 / 1024:.2f}"


def output_file_info_payload():
    if not OUTPUT_FILE.exists():
        return {"exists": False}
    size_bytes = OUTPUT_FILE.stat().st_size
    return {
        "exists": True,
        "filename": "final.mp4",
        "relative_path": "output/final.mp4",
        "absolute_path": str(OUTPUT_FILE.resolve()),
        "size_bytes": size_bytes,
        "size_mb": f"{size_bytes / 1024 / 1024:.2f}",
        "preview_url": "/preview_video",
        "download_url": "/download",
    }


def backend_system_payload():
    return {
        "os": platform.system() or "Unknown",
        "os_release": platform.release() or "",
        "os_version": platform.version() or "",
        "machine": platform.machine() or "",
        "processor": platform.processor() or "",
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "project_dir": str(BASE_DIR.resolve()),
    }


def content_compliance_summary():
    script = load_script()
    prompt_output = read_prompt_output()
    checks = [
        ("图片提示词", prompt_output),
        ("视频标题", script.get("title", "")),
        ("字幕文案", "\n".join(script.get("subtitles") or [])),
        ("旁白文本", script.get("voiceover", "")),
    ]
    issues = []
    for name, text in checks:
        result = scan_content(text)
        if not result["ok"]:
            issues.append({"source": name, "findings": result["findings"]})
    recent_checks = list(compliance_events)[:20]
    recent_alerts = [event for event in recent_checks if event.get("status") != "通过"]
    needs_review = bool(issues or recent_alerts)
    return {
        "status": "需要复核" if needs_review else "通过",
        "message": "发现敏感词命中或近期拦截记录，请人工复核。" if needs_review else "当前已保存文本未发现敏感词命中。",
        "issues": issues,
        "image_file_count": get_image_count(),
        "last_checks": recent_checks,
    }


def backend_dashboard_payload():
    progress = read_progress()
    progress["running"] = generation_is_running()
    with dashboard_lock:
        api_calls = list(api_call_events)[:50]
    return {
        "system": backend_system_payload(),
        "generation": progress,
        "api_calls": api_calls,
        "compliance": content_compliance_summary(),
        "logs": read_system_logs(80),
    }


def load_history():
    ensure_history_dirs()
    try:
        records = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict) and r.get("id") and r.get("filename")]


def save_history(records):
    ensure_history_dirs()
    HISTORY_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def history_record_with_urls(record):
    item = dict(record)
    video_id = item.get("id", "")
    item["preview_url"] = f"/history/preview/{video_id}"
    item["download_url"] = f"/history/download/{video_id}"
    return item


def get_history_records_payload():
    return [history_record_with_urls(record) for record in load_history()]


def guess_destination_from_script():
    script = load_script()
    title = (script.get("title") or "").strip()
    if title:
        return title
    subtitles = script.get("subtitles") or []
    if isinstance(subtitles, list):
        for line in subtitles:
            line = str(line).strip()
            if line:
                return line[:30]
    voiceover = (script.get("voiceover") or "").strip()
    if voiceover:
        return voiceover.splitlines()[0][:30]
    return "未命名视频"


def add_video_to_history():
    ensure_history_dirs()
    if not OUTPUT_FILE.exists():
        raise FileNotFoundError("output/final.mp4 不存在，无法加入历史记录。")
    now = datetime.now()
    base_id = now.strftime("%Y%m%d_%H%M%S")
    video_id = base_id
    filename = f"video_{video_id}.mp4"
    target = HISTORY_VIDEO_DIR / filename
    counter = 1
    while target.exists():
        video_id = f"{base_id}_{counter}"
        filename = f"video_{video_id}.mp4"
        target = HISTORY_VIDEO_DIR / filename
        counter += 1
    shutil.copy2(OUTPUT_FILE, target)
    script = load_script()
    record = {
        "id": video_id,
        "filename": filename,
        "title": (script.get("title") or "").strip() or "未命名视频",
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "relative_path": f"history/videos/{filename}",
        "size_mb": get_video_size_mb(target),
        "duration_per_image": script.get("duration_per_image", 6),
        "image_count": get_image_count(),
        "destination": guess_destination_from_script(),
    }
    records = load_history()
    records.insert(0, record)
    save_history(records)
    return history_record_with_urls(record)


def find_history_record(video_id):
    video_id = Path(video_id or "").name
    for record in load_history():
        if record.get("id") == video_id:
            return record
    return None


def history_video_path(record):
    filename = Path(record.get("filename", "")).name
    path = (HISTORY_VIDEO_DIR / filename).resolve()
    if path.parent != HISTORY_VIDEO_DIR.resolve():
        raise ValueError("历史视频路径不合法。")
    return path


def status_payload():
    voice_exists = (
        (OUTPUT_DIR / "voice.mp3").exists()
        or any(OUTPUT_DIR.glob("voice_*.mp3"))
        or any(OUTPUT_DIR.glob("voice_*.wav"))
        or any(OUTPUT_DIR.glob("voice_run_*/*.mp3"))
        or any(OUTPUT_DIR.glob("voice_run_*/*.wav"))
    )
    return {
        "image_files": get_image_files(),
        "has_output": OUTPUT_FILE.exists(),
        "bgm_exists": (AUDIO_DIR / "bgm.mp3").exists(),
        "voice_exists": voice_exists,
        "prompt_output": read_prompt_output(),
        "script": load_script(),
        "prompt_form": default_prompt_form(),
        "output_file": output_file_info_payload(),
        "backend_system": backend_system_payload(),
        "backend_dashboard": backend_dashboard_payload(),
    }


def next_image_index():
    max_index = 0
    for p in IMAGE_DIR.iterdir():
        if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE_EXTS and p.stem.isdigit():
            max_index = max(max_index, int(p.stem))
    return max_index + 1


def safe_image_path(filename):
    filename = Path(filename or "").name
    if Path(filename).suffix.lower() not in ALLOWED_IMAGE_EXTS:
        return None
    image_dir = IMAGE_DIR.resolve()
    image_path = (IMAGE_DIR / filename).resolve()
    return image_path if image_path.parent == image_dir else None


def resolve_target_dir(target_dir):
    target_dir = (target_dir or "").strip()
    target = EXPORTS_DIR if not target_dir else Path(target_dir).expanduser()
    if not target.is_absolute():
        target = BASE_DIR / target
    target = target.resolve()
    if target.exists() and not target.is_dir():
        raise ValueError("目标路径已经存在，但不是文件夹。")
    target.mkdir(parents=True, exist_ok=True)
    return target


def timestamped_output_name():
    return f"final_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"


def generation_is_running():
    return generation_thread is not None and generation_thread.is_alive()


def run_video_generation_job(video_engine="native"):
    try:
        append_system_log("INFO", "视频生成任务开始", {"image_count": get_image_count(), "video_engine": video_engine})
        write_progress(5, "正在读取图片并启动视频生成...")
        env = os.environ.copy()
        env["VIDEO_RENDER_ENGINE"] = video_engine
        result = subprocess.run([sys.executable, "main.py"], cwd=BASE_DIR, env=env, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "未知错误").strip()
            write_progress(100, "生成失败", done=True, error=error_text[-3000:])
            return

        history_record = None
        history_warning = ""
        if OUTPUT_FILE.exists():
            try:
                history_record = add_video_to_history()
            except (OSError, ValueError, FileNotFoundError) as exc:
                history_warning = f"视频已生成，但保存历史记录失败：{exc}"

        if history_warning:
            write_progress(100, history_warning, done=True)
        else:
            write_progress(100, "视频生成完成，已保存到历史记录。", done=True)
        append_system_log("INFO", "视频生成任务结束", {"returncode": result.returncode, "history_saved": bool(history_record)})
    except Exception as exc:
        write_progress(100, "生成失败", done=True, error=str(exc)[-3000:])
        append_system_log("ERROR", "视频生成任务异常", {"error": str(exc)})
    finally:
        generation_lock.release()


def split_lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def extract_text_prompts(markdown):
    prompts = []
    in_block = False
    block_lines = []
    for raw_line in (markdown or "").splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            if in_block:
                prompt = "\n".join(block_lines).strip()
                if prompt:
                    prompts.append(prompt)
                block_lines = []
                in_block = False
            else:
                in_block = line.lower() == "```text"
            continue
        if in_block:
            block_lines.append(raw_line)
    return prompts


def save_generated_image_bytes(image_bytes, index):
    ensure_dirs()
    filename = f"{index:02d}.png"
    target = (IMAGE_DIR / filename).resolve()
    if target.parent != IMAGE_DIR.resolve():
        raise ValueError("图片保存路径不合法。")
    target.write_bytes(image_bytes)
    try:
        with Image.open(target) as image:
            image.verify()
    except (OSError, UnidentifiedImageError):
        target.unlink(missing_ok=True)
        raise RuntimeError("生图接口返回的内容不是有效图片。")
    return filename


def validate_image_upload(file):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_IMAGE_EXTS:
        return False, f"仅支持 {ALLOWED_IMAGE_FORMAT_LABEL}"
    try:
        file.stream.seek(0)
        with Image.open(file.stream) as image:
            image.verify()
        file.stream.seek(0)
    except (OSError, UnidentifiedImageError):
        return False, "文件内容不是有效图片"
    return True, ""


def save_reference_images(files):
    ensure_dirs()
    REFERENCE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    request_dir = REFERENCE_IMAGE_DIR / f"{int(time.time() * 1000)}-{threading.get_ident()}"
    request_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    skipped = []
    for index, file in enumerate(files[:MAX_IMAGE_BATCH], start=1):
        if not file or not file.filename:
            continue
        valid, reason = validate_image_upload(file)
        if not valid:
            skipped.append({"filename": file.filename, "reason": reason})
            continue
        suffix = Path(file.filename).suffix.lower()
        target = request_dir / f"{index:02d}{suffix}"
        file.save(target)
        saved.append(target)
    return request_dir, saved, skipped


def build_prompt_document(destination, aspect_ratio, style_keywords, negative_keywords, scenes):
    lines = [
        f"# {destination} 旅游伪视频图片提示词",
        "",
        f"画幅比例：{aspect_ratio}",
        "",
        "说明：本项目不直接调用生图 API。请把下面每条提示词复制到外部图片生成工具，生成后回到网页上传图片。",
        "",
        "统一风格要求：",
        style_keywords,
        "",
        "统一负面约束：",
        negative_keywords,
        "",
        "---",
        "",
    ]
    for index, scene in enumerate(scenes, start=1):
        filename = f"{index:02d}.jpg"
        prompt = f"{destination}，{scene}，真实摄影，电影感旅行纪录片，自然光，画面干净，色彩统一，适合图片运镜短视频，{aspect_ratio}，{style_keywords}，{negative_keywords}"
        lines.extend([f"## {filename} {scene}", "", "```text", prompt, "```", ""])
    return "\n".join(lines)


@app.route("/")
def index():
    ensure_dirs()
    return render_template("index.html", prompt_form=default_prompt_form(), prompt_output=read_prompt_output(), data=load_script(), image_files=get_image_files(), output_file=output_file_info_payload())


@app.route("/api/status")
def api_status():
    return api_ok("状态已更新。", status_payload())


@app.route("/api/generate_prompts", methods=["POST"])
def api_generate_prompts():
    destination = request.form.get("destination", "").strip()
    if not destination:
        return api_error("请先输入目的地 / 主题。")

    aspect_ratio = request.form.get("aspect_ratio", "16:9").strip() or "16:9"
    style_keywords = request.form.get("style_keywords", DEFAULT_STYLE_KEYWORDS).strip()
    negative_keywords = request.form.get("negative_keywords", DEFAULT_NEGATIVE_KEYWORDS).strip()
    scenes = split_lines(request.form.get("scenes_text", "")) or DEFAULT_SCENES
    blocked = require_clean_text(
        "图片提示词输入",
        {
            "destination": destination,
            "style_keywords": style_keywords,
            "negative_keywords": negative_keywords,
            "scenes": "\n".join(scenes),
        },
    )
    if blocked:
        return blocked

    try:
        duration_per_image = float(request.form.get("duration_per_image", load_script().get("duration_per_image", 6)))
    except (TypeError, ValueError):
        duration_per_image = 6
    duration_per_image = max(1, duration_per_image)

    try:
        image_count = int(request.form.get("image_count", ""))
    except (TypeError, ValueError):
        image_count = len(scenes) or len(get_image_files()) or 8
    image_count = max(1, min(MAX_IMAGE_BATCH, image_count))

    reference_files = request.files.getlist("reference_images")
    reference_analysis = []
    reference_skipped = []
    use_web_search = request.form.get("use_web_search") == "1"
    web_search_query = request.form.get("web_search_query", "").strip() or destination
    web_context = None
    web_search_payload = {"enabled": use_web_search, "used": False, "warning": "", "query": web_search_query, "results": []}
    if reference_files:
        reference_dir, reference_paths, reference_skipped = save_reference_images(reference_files)
        if reference_paths:
            try:
                append_system_log("INFO", "调用 Qwen-VL 分析提示词参考图", {"image_count": len(reference_paths)})
                reference_analysis = analyze_images_with_qwen_vl(reference_paths)
            except Exception as exc:
                append_system_log("ERROR", "提示词参考图视觉分析失败", {"error": str(exc)})
                shutil.rmtree(reference_dir, ignore_errors=True)
                return api_error(f"参考图视觉分析失败：{exc}", {"reference_skipped": reference_skipped}, status=502)
            finally:
                shutil.rmtree(reference_dir, ignore_errors=True)
        else:
            shutil.rmtree(reference_dir, ignore_errors=True)
            return api_error(f"参考图没有上传成功，请选择有效的 {ALLOWED_IMAGE_FORMAT_LABEL} 文件。", {"reference_skipped": reference_skipped}, status=400)

    if use_web_search:
        try:
            append_system_log("INFO", "联网检索目的地事实", {"query": web_search_query})
            web_context = search_destination_web_context(web_search_query)
            web_search_payload.update({
                "used": True,
                "query": web_context.get("query", web_search_query),
                "results": web_context.get("results", []),
            })
        except Exception as exc:
            warning = str(exc)
            web_search_payload["warning"] = warning
            append_system_log("WARN", "联网检索失败，继续按现有信息生成提示词", {"error": warning})

    append_system_log(
        "INFO",
        "调用提示词生成 API",
        {
            "destination": destination,
            "image_count": image_count,
            "reference_image_count": len(reference_analysis),
            "web_search_used": web_search_payload["used"],
        },
    )
    try:
        prompt_output = generate_prompt_markdown_with_api(
            destination=destination,
            aspect_ratio=aspect_ratio,
            style_keywords=style_keywords,
            negative_keywords=negative_keywords,
            scenes=scenes,
            timing_plan={
                "image_count": image_count,
                "duration_per_image": duration_per_image,
                "video_duration": image_count * duration_per_image,
            },
            reference_analysis=reference_analysis,
            web_context=web_context,
        )
    except Exception as exc:
        append_system_log("ERROR", "提示词生成 API 调用失败", {"error": str(exc)})
        return api_error(f"图片提示词生成失败：{exc}", {"reference_analysis": reference_analysis}, status=502)
    prompt_scan = scan_content(prompt_output)
    if not prompt_scan["ok"]:
        record_compliance_event("图片提示词输出", "需要复核", "生成提示词命中基础合规规则，已保存前拦截。", prompt_scan)
        return api_error("AI 生成提示词合规检查未通过，请调整主题后重试。", {"compliance": prompt_scan}, status=400)
    record_compliance_event("图片提示词输出", "通过", "提示词已生成并完成基础合规扫描。")
    PROMPTS_FILE.write_text(prompt_output, encoding="utf-8")
    message = "图片提示词已生成，并保存到 IMAGE_PROMPTS.md。"
    if reference_analysis:
        message += f" 已参考 {len(reference_analysis)} 张现实图片的视觉分析。"
    if web_search_payload["used"]:
        message += f" 已联网参考 {len(web_search_payload['results'])} 条地点资料。"
    elif use_web_search and web_search_payload["warning"]:
        message += " 联网检索不可用，已继续使用当前输入生成。"
    return api_ok(
        message,
        {
            "prompt_output": prompt_output,
            "reference_analysis": reference_analysis,
            "reference_skipped": reference_skipped,
            "web_search": web_search_payload,
            "prompt_form": {
                "destination": destination,
                "aspect_ratio": aspect_ratio,
                "style_keywords": style_keywords,
                "negative_keywords": negative_keywords,
                "scenes_text": "\n".join(scenes),
            },
        },
    )


@app.route("/api/generate_script", methods=["POST"])
def api_generate_script():
    destination = request.form.get("destination", "").strip() or "旅行目的地"
    tone_label = request.form.get("tone_label", "治愈文艺").strip() or "治愈文艺"
    blocked = require_clean_text("文案生成输入", {"destination": destination, "tone_label": tone_label})
    if blocked:
        return blocked
    try:
        duration = float(request.form.get("duration_per_image", "6"))
    except ValueError:
        duration = 6
    duration = max(1, duration)
    try:
        image_count = int(request.form.get("image_count", ""))
    except (TypeError, ValueError):
        image_count = len(get_image_files()) or 8

    image_files = [IMAGE_DIR / name for name in get_image_files()]
    try:
        append_system_log("INFO", "按当前图片生成文案", {"destination": destination, "image_count": image_count, "tone": tone_label})
        if image_files:
            try:
                image_analysis = analyze_images_with_qwen_vl(image_files[:image_count])
                result = write_script_with_deepseek(image_analysis, destination, duration, tone_label)
            except Exception as exc:
                append_system_log("WARN", "图片视觉分析失败，改用提示词镜头标题生成文案", {"error": str(exc)})
                result = script_from_prompt_titles(destination, image_count, duration, tone_label)
        else:
            result = write_city_script_with_deepseek(destination, image_count, duration, tone_label)
    except Exception as exc:
        append_system_log("ERROR", "文案生成 API 调用失败", {"error": str(exc)})
        return api_error(f"AI 文案生成失败：{exc}", status=500)

    script = {
        "title": result.get("title", f"{destination}慢旅行"),
        "duration_per_image": duration,
        "aspect_ratio": "16:9",
        "subtitles": result.get("subtitles", []),
        "voiceover": "\n".join(result.get("voiceover", [])),
        "xiaohongshu_title": result.get("xiaohongshu_title", ""),
        "video_description": result.get("video_description", ""),
        "image_order_advice": result.get("image_order_advice", ""),
        "tone_label": tone_label,
    }
    script_scan = scan_content(json.dumps(script, ensure_ascii=False))
    if not script_scan["ok"]:
        record_compliance_event("AI 文案输出", "需要复核", "生成文案命中基础合规规则，已保存前拦截。", script_scan)
        return api_error("AI 生成文案合规检查未通过，请调整主题后重试。", {"compliance": script_scan}, status=400)
    record_compliance_event("AI 文案输出", "通过", "生成文案未发现敏感词命中。")
    save_script(script)
    return api_ok("AI 文案已生成，并保存到 script.json。", {"script": script, "raw": result})


@app.route("/api/upload_images", methods=["POST"])
def api_upload_images():
    ensure_dirs()
    files = request.files.getlist("images")
    if len(files) > MAX_UPLOAD_FILES:
        record_compliance_event("图片上传", "拦截", "一次上传文件数量超过限制。", {"file_count": len(files), "limit": MAX_UPLOAD_FILES})
        return api_error(f"一次最多上传 {MAX_UPLOAD_FILES} 个文件。", {"image_files": get_image_files()})
    index = next_image_index()
    saved = 0
    skipped = []
    for file in files:
        if not file or not file.filename:
            continue
        valid, reason = validate_image_upload(file)
        if not valid:
            record_compliance_event("图片上传", "拦截", reason, {"filename": file.filename})
            skipped.append({"filename": file.filename, "reason": reason})
            continue
        suffix = Path(file.filename).suffix.lower()
        file.save(IMAGE_DIR / f"{index:02d}{suffix}")
        index += 1
        saved += 1
    if not saved:
        return api_error(f"没有上传成功的图片，请选择有效的 {ALLOWED_IMAGE_FORMAT_LABEL} 文件。", {"image_files": get_image_files(), "skipped": skipped})
    record_compliance_event("图片上传", "通过", "上传图片格式校验通过。", {"saved": saved, "skipped": len(skipped)})
    message = f"已上传 {saved} 张图片。"
    if skipped:
        message += f" 跳过 {len(skipped)} 个不支持或无效的文件。"
    return api_ok(message, {"image_files": get_image_files(), "skipped": skipped})


@app.route("/api/generate_openai_images", methods=["POST"])
def api_generate_openai_images():
    ensure_dirs()
    prompt_text = request.form.get("prompt_output", "").strip() or read_prompt_output()
    blocked = require_clean_text("OpenAI 生图提示词", {"prompt_output": prompt_text})
    if blocked:
        return blocked
    prompts = extract_text_prompts(prompt_text)
    if not prompts:
        return api_error("没有找到可用于生图的 ```text 提示词块，请先在第 1 步重新生成图片提示词。")

    try:
        requested_count = int(request.form.get("image_count", len(prompts)))
    except ValueError:
        requested_count = len(prompts)
    requested_count = max(1, min(requested_count, len(prompts), MAX_IMAGE_BATCH))

    index = next_image_index()
    saved = []
    errors = []
    for prompt in prompts[:requested_count]:
        try:
            append_system_log("INFO", "调用 OpenAI 生图 API", {"index": index})
            image_bytes = generate_image_with_openai(prompt)
            filename = save_generated_image_bytes(image_bytes, index)
            saved.append(filename)
            index += 1
        except Exception as exc:
            append_system_log("ERROR", "OpenAI 生图 API 调用失败", {"error": str(exc)})
            errors.append(str(exc))
            break

    if not saved:
        message = errors[0] if errors else "OpenAI 生图失败。"
        return api_error(message, {"image_files": get_image_files(), "errors": errors}, status=500)

    message = f"OpenAI 已生成 {len(saved)} 张图片，并保存到 images 文件夹。"
    if errors:
        message += f" 另有 {len(errors)} 个任务失败：{errors[0]}"
    return api_ok(message, {"image_files": get_image_files(), "saved": saved, "errors": errors})


@app.route("/api/generate_local_images", methods=["POST"])
def api_generate_local_images():
    ensure_dirs()
    prompt_text = request.form.get("prompt_output", "").strip() or read_prompt_output()
    blocked = require_clean_text("本地模型生图提示词", {"prompt_output": prompt_text})
    if blocked:
        return blocked
    prompts = extract_text_prompts(prompt_text)
    if not prompts:
        return api_error("没有找到可用于生图的 ```text 提示词块，请先在第 1 步重新生成图片提示词。")

    try:
        requested_count = int(request.form.get("image_count", len(prompts)))
    except ValueError:
        requested_count = len(prompts)
    requested_count = max(1, min(requested_count, len(prompts), MAX_IMAGE_BATCH))

    index = next_image_index()
    saved = []
    errors = []
    for prompt in prompts[:requested_count]:
        try:
            append_system_log("INFO", "调用本地图片模型", {"index": index})
            image_bytes = generate_image_with_local_model(prompt)
            filename = save_generated_image_bytes(image_bytes, index)
            saved.append(filename)
            index += 1
        except Exception as exc:
            append_system_log("ERROR", "本地图片模型调用失败", {"error": str(exc)})
            errors.append(str(exc))
            break

    if not saved:
        message = errors[0] if errors else "本地图片模型生图失败。"
        return api_error(message, {"image_files": get_image_files(), "errors": errors}, status=500)

    message = f"本地图片模型已生成 {len(saved)} 张图片，并保存到 images 文件夹。"
    if errors:
        message += f" 另有 {len(errors)} 个任务失败：{errors[0]}"
    return api_ok(message, {"image_files": get_image_files(), "saved": saved, "errors": errors})


@app.route("/api/delete_image", methods=["POST"])
def api_delete_image():
    image_path = safe_image_path(request.form.get("filename", ""))
    if not image_path or not image_path.exists():
        return api_error(f"删除失败：只能删除 images 文件夹内存在的 {ALLOWED_IMAGE_FORMAT_LABEL} 图片。", {"image_files": get_image_files()})
    image_path.unlink()
    return api_ok(f"已删除图片：{image_path.name}", {"image_files": get_image_files()})


@app.route("/api/clear_images", methods=["POST"])
def api_clear_images():
    ensure_dirs()
    deleted = 0
    for p in IMAGE_DIR.iterdir():
        if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE_EXTS:
            p.unlink()
            deleted += 1
    return api_ok(f"已清空图片，共删除 {deleted} 张。", {"image_files": get_image_files()})


@app.route("/api/save_script", methods=["POST"])
def api_save_script():
    try:
        duration = float(request.form.get("duration_per_image", "6"))
    except ValueError:
        duration = 6
    data = {
        "title": request.form.get("title", "").strip(),
        "duration_per_image": max(1, duration),
        "aspect_ratio": "16:9",
        "subtitles": split_lines(request.form.get("subtitles", "")),
        "voiceover": request.form.get("voiceover", "").strip(),
        "tone_label": request.form.get("tone_label", "治愈文艺").strip() or "治愈文艺",
        "video_engine": request.form.get("video_engine", "native").strip() or "native",
    }
    blocked = require_clean_text(
        "手动保存文案",
        {"title": data["title"], "subtitles": "\n".join(data["subtitles"]), "voiceover": data["voiceover"]},
    )
    if blocked:
        return blocked
    save_script(data)
    return api_ok("文案已保存到 script.json。生成视频时会烧录字幕并合成旁白声音。", {"script": data})


@app.route("/api/generate_video", methods=["POST"])
def api_generate_video():
    global generation_thread
    video_engine = request.form.get("video_engine", "native").strip() or "native"
    allowed_engines = {"native", "kburns-slideshow", "3d-ken-burns"}
    if video_engine not in allowed_engines:
        return api_error("不支持的视频引擎。", {"allowed_engines": sorted(allowed_engines)}, status=400)
    if not get_image_files():
        write_progress(0, "请先上传至少一张图片，再生成视频。", done=True, error="没有可用图片")
        return api_error("请先上传至少一张图片，再生成视频。", {"has_output": OUTPUT_FILE.exists()})
    if not generation_lock.acquire(blocking=False):
        return api_error("已有视频正在生成，请等待当前任务完成。", read_progress(), status=409)

    write_progress(0, "生成任务已启动，请稍等...")
    generation_thread = threading.Thread(target=run_video_generation_job, args=(video_engine,), daemon=True)
    generation_thread.start()
    return api_ok("生成任务已启动。", {"running": True, "progress": read_progress()})


@app.route("/api/generate_progress")
def api_generate_progress():
    data = read_progress()
    data["has_output"] = OUTPUT_FILE.exists()
    data["running"] = generation_is_running()
    return api_ok("读取成功", data)


@app.route("/api/output_file_info")
def api_output_file_info():
    data = output_file_info_payload()
    return api_ok("视频文件已生成" if data.get("exists") else "还没有生成视频", data)


@app.route("/api/backend_system")
def api_backend_system():
    return api_ok("后端操作系统信息读取成功", backend_system_payload())


@app.route("/api/backend_dashboard")
def api_backend_dashboard():
    return api_ok("后端系统状态读取成功", backend_dashboard_payload())


@app.route("/api/copy_output", methods=["POST"])
def api_copy_output():
    if not OUTPUT_FILE.exists():
        return api_error("还没有生成 output/final.mp4，无法复制。", {"exists": False}, status=404)
    try:
        target = resolve_target_dir(request.form.get("target_dir", ""))
        copied_path = target / timestamped_output_name()
        shutil.copy2(OUTPUT_FILE, copied_path)
    except (OSError, ValueError) as exc:
        return api_error(f"复制失败：{exc}", status=500)
    return api_ok("视频已复制", {"copied_path": str(copied_path.resolve())})


@app.route("/api/move_output", methods=["POST"])
def api_move_output():
    if not OUTPUT_FILE.exists():
        return api_error("还没有生成 output/final.mp4，无法移动。", {"exists": False}, status=404)
    try:
        target = resolve_target_dir(request.form.get("target_dir", ""))
        moved_path = target / timestamped_output_name()
        shutil.move(str(OUTPUT_FILE), str(moved_path))
    except (OSError, ValueError) as exc:
        return api_error(f"移动失败：{exc}", status=500)
    return api_ok("视频已移动，项目输出目录里不再保留 final.mp4。", {"moved_path": str(moved_path.resolve()), "output_file": output_file_info_payload()})


@app.route("/api/history")
def api_history():
    return api_ok("读取历史记录成功", {"records": get_history_records_payload()})


@app.route("/history/preview/<video_id>")
def history_preview(video_id):
    record = find_history_record(video_id)
    if not record:
        return "历史视频不存在。", 404
    try:
        path = history_video_path(record)
    except ValueError:
        return "历史视频路径不合法。", 400
    if not path.exists():
        return "历史视频文件不存在。", 404
    return send_file(path, mimetype="video/mp4", as_attachment=False)


@app.route("/history/download/<video_id>")
def history_download(video_id):
    record = find_history_record(video_id)
    if not record:
        return "历史视频不存在。", 404
    try:
        path = history_video_path(record)
    except ValueError:
        return "历史视频路径不合法。", 400
    if not path.exists():
        return "历史视频文件不存在。", 404
    return send_file(path, as_attachment=True, download_name=record.get("filename") or path.name)


@app.route("/api/history/delete", methods=["POST"])
def api_history_delete():
    video_id = Path(request.form.get("video_id", "")).name
    records = load_history()
    record = next((item for item in records if item.get("id") == video_id), None)
    if not record:
        return api_error("历史视频不存在。", {"records": get_history_records_payload()}, status=404)
    try:
        path = history_video_path(record)
        if path.exists():
            path.unlink()
    except (OSError, ValueError) as exc:
        return api_error(f"删除历史视频失败：{exc}", status=500)
    records = [item for item in records if item.get("id") != video_id]
    save_history(records)
    return api_ok("历史视频已删除", {"records": get_history_records_payload()})


@app.route("/api/history/clear", methods=["POST"])
def api_history_clear():
    ensure_history_dirs()
    history_dir = HISTORY_VIDEO_DIR.resolve()
    for video_path in HISTORY_VIDEO_DIR.glob("*.mp4"):
        if video_path.is_file() and video_path.resolve().parent == history_dir:
            video_path.unlink()
    save_history([])
    return api_ok("历史记录已清空", {"records": []})


@app.route("/preview_video")
def preview_video():
    if not OUTPUT_FILE.exists():
        return "还没有生成 output/final.mp4。", 404
    return send_file(OUTPUT_FILE, mimetype="video/mp4", as_attachment=False)


@app.route("/download")
def download():
    if not OUTPUT_FILE.exists():
        return "还没有生成 output/final.mp4。", 404
    return send_file(OUTPUT_FILE, as_attachment=True, download_name="final.mp4")


if __name__ == "__main__":
    ensure_dirs()
    app.run(host="127.0.0.1", port=5000, debug=True)
