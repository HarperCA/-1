from pathlib import Path
import json
import shutil
import subprocess
import sys

from flask import Flask, render_template, request, send_file, redirect, url_for, flash

BASE_DIR = Path(__file__).parent
IMAGE_DIR = BASE_DIR / "images"
AUDIO_DIR = BASE_DIR / "audio"
OUTPUT_DIR = BASE_DIR / "output"
SCRIPT_FILE = BASE_DIR / "script.json"
OUTPUT_FILE = OUTPUT_DIR / "final.mp4"

app = Flask(__name__)
app.secret_key = "travel-video-generator"

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def ensure_dirs():
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_script():
    if SCRIPT_FILE.exists():
        return json.loads(SCRIPT_FILE.read_text(encoding="utf-8"))
    return {
        "title": "慢慢抵达一座城",
        "duration_per_image": 6,
        "aspect_ratio": "16:9",
        "subtitles": [],
        "voiceover": ""
    }


def save_script(data):
    SCRIPT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@app.route("/", methods=["GET"])
def index():
    ensure_dirs()
    data = load_script()
    image_files = sorted([p.name for p in IMAGE_DIR.iterdir() if p.suffix.lower() in ALLOWED_IMAGE_EXTS])
    has_output = OUTPUT_FILE.exists()
    return render_template("index.html", data=data, image_files=image_files, has_output=has_output)


@app.route("/save", methods=["POST"])
def save():
    ensure_dirs()
    title = request.form.get("title", "慢慢抵达一座城").strip()
    duration = int(request.form.get("duration_per_image", "6"))
    subtitles_text = request.form.get("subtitles", "").strip()
    voiceover = request.form.get("voiceover", "").strip()

    subtitles = [line.strip() for line in subtitles_text.splitlines() if line.strip()]

    data = {
        "title": title,
        "duration_per_image": duration,
        "aspect_ratio": "16:9",
        "subtitles": subtitles,
        "voiceover": voiceover or "\n".join(subtitles)
    }
    save_script(data)
    flash("脚本和字幕已保存。")
    return redirect(url_for("index"))


@app.route("/upload", methods=["POST"])
def upload():
    ensure_dirs()
    files = request.files.getlist("images")
    saved = 0

    # 清空旧图片，避免混用
    for old_file in IMAGE_DIR.iterdir():
        if old_file.suffix.lower() in ALLOWED_IMAGE_EXTS:
            old_file.unlink()

    for index, file in enumerate(files, start=1):
        if not file or not file.filename:
            continue
        suffix = Path(file.filename).suffix.lower()
        if suffix not in ALLOWED_IMAGE_EXTS:
            continue
        filename = f"{index:02d}{suffix}"
        file.save(IMAGE_DIR / filename)
        saved += 1

    flash(f"已上传 {saved} 张图片。")
    return redirect(url_for("index"))


@app.route("/upload_bgm", methods=["POST"])
def upload_bgm():
    ensure_dirs()
    file = request.files.get("bgm")
    if file and file.filename:
        file.save(AUDIO_DIR / "bgm.mp3")
        flash("背景音乐已上传：audio/bgm.mp3")
    else:
        flash("没有选择背景音乐文件。")
    return redirect(url_for("index"))


@app.route("/generate", methods=["POST"])
def generate():
    ensure_dirs()
    try:
        result = subprocess.run(
            [sys.executable, "main.py"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )
        if result.returncode != 0:
            flash("生成失败：\n" + result.stderr[-1000:])
        else:
            flash("视频生成成功：output/final.mp4")
    except Exception as exc:
        flash(f"生成失败：{exc}")
    return redirect(url_for("index"))


@app.route("/download")
def download():
    if not OUTPUT_FILE.exists():
        flash("还没有生成视频。")
        return redirect(url_for("index"))
    return send_file(OUTPUT_FILE, as_attachment=True)


if __name__ == "__main__":
    ensure_dirs()
    app.run(host="127.0.0.1", port=5000, debug=True)
