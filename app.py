from pathlib import Path
import json
import subprocess
import sys

from flask import Flask, render_template, request, send_file, redirect, url_for, flash

BASE_DIR = Path(__file__).parent
IMAGE_DIR = BASE_DIR / "images"
AUDIO_DIR = BASE_DIR / "audio"
OUTPUT_DIR = BASE_DIR / "output"
SCRIPT_FILE = BASE_DIR / "script.json"
OUTPUT_FILE = OUTPUT_DIR / "final.mp4"
PROMPTS_FILE = BASE_DIR / "IMAGE_PROMPTS.md"

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
        "title": "慢慢抵达泉州古城",
        "duration_per_image": 6,
        "aspect_ratio": "16:9",
        "subtitles": [],
        "voiceover": ""
    }


def save_script(data):
    SCRIPT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def default_prompt_form():
    return {
        "destination": "泉州古城",
        "aspect_ratio": "16:9",
        "style_keywords": "真实摄影、电影感、纪录片风格、自然光、慢节奏文旅宣传片、高清、真实细节、自然色彩、干净画面、远景人物、背影、无清晰面部",
        "negative_keywords": "避免文字、避免水印、避免Logo、避免插画风、避免卡通感、避免AI感、避免乱码招牌、避免错误建筑结构、避免不自然建筑比例、避免近景人像、避免清晰面部、避免夸张肢体、避免过度滤镜",
        "scenes_text": "\n".join([
            "古城清晨",
            "西街老巷",
            "红砖古厝",
            "开元寺双塔",
            "街头生活感",
            "簪花古巷",
            "旅人背影",
            "傍晚收尾"
        ])
    }


def read_prompt_output():
    if PROMPTS_FILE.exists():
        return PROMPTS_FILE.read_text(encoding="utf-8")
    return ""


def image_path_from_name(filename):
    """根据文件名获取 images 目录下的安全图片路径，防止误删目录外文件。"""
    if not filename:
        return None

    filename = Path(filename).name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_IMAGE_EXTS:
        return None

    image_dir = IMAGE_DIR.resolve()
    image_path = (IMAGE_DIR / filename).resolve()
    if image_path.parent != image_dir:
        return None

    return image_path


def scene_detail(destination, scene_name):
    name = scene_name.strip()

    if "清晨" in name:
        return f"清晨的{destination}真实摄影远景，老街巷铺展开来，远处有地标轮廓，晨光柔和，空气通透，树影落在屋檐和石板路上，画面安静怀旧"
    if "西街" in name or "老巷" in name:
        return f"{destination}老街巷真实摄影画面，传统建筑、石板路、街边小店与远景人物自然入镜，温暖自然光，生活气息真实"
    if "红砖" in name or "古厝" in name:
        return f"{destination}闽南红砖古厝真实摄影，老屋屋檐、红砖墙、石板路和斑驳墙面，阳光从巷子上方斜斜落下，建筑细节真实，安静怀旧，浅景深"
    if "双塔" in name or "开元寺" in name or "寺" in name:
        return f"{destination}开元寺双塔真实摄影，古老石塔立在寺庙庭院中，树影斑驳，建筑比例真实，画面庄重安静，有历史感"
    if "生活" in name or "小吃" in name or "街头" in name:
        return f"{destination}街头生活场景真实摄影，街边小店、传统小吃摊、自然行走的远景人物，画面温暖克制，具有城市日常感"
    if "簪花" in name:
        return f"闽南簪花人物背影走在{destination}红砖古厝巷子里，传统簪花头饰，阳光照在红砖墙面和石板路上，画面温柔安静，有地方文化气息"
    if "旅人" in name or "背影" in name:
        return f"旅人背影慢慢走过{destination}老巷，墙面斑驳，石板路上有温暖阳光，巷子安静治愈，城市漫游感明显"
    if "傍晚" in name or "收尾" in name or "黄昏" in name:
        return f"傍晚的{destination}远景真实摄影，城市建筑安静铺展，天空微橙，远处光线柔和，诗意文旅宣传片结尾画面"

    return f"{destination}{name}真实摄影画面，保留地域文化特色与生活气息，画面自然、安静、适合文旅宣传片"


def get_orientation_text(aspect_ratio):
    ratio = aspect_ratio.replace(" ", "")
    if ratio == "9:16":
        return "竖屏"
    if ratio == "1:1":
        return "方图"
    return "横屏"


def build_single_prompt(destination, scene_name, style_keywords, negative_keywords, aspect_ratio):
    detail = scene_detail(destination, scene_name)
    orientation = get_orientation_text(aspect_ratio)
    safe_people_text = "人物只作为远景或背影出现，无清晰面部"
    return f"{detail}，{style_keywords}，{safe_people_text}，{aspect_ratio}{orientation}，{negative_keywords}"


def build_prompt_document(destination, aspect_ratio, style_keywords, negative_keywords, scenes):
    lines = []
    lines.append(f"# {destination}旅游伪视频图片生成提示词｜{aspect_ratio}版")
    lines.append("")
    lines.append("用途：用于即梦、豆包、通义万相、可灵图片等工具生成旅游短视频配图。")
    lines.append("")
    lines.append("建议统一参数：")
    lines.append("")
    lines.append("```text")
    lines.append(f"目的地：{destination}")
    lines.append(f"画幅比例：{aspect_ratio}")
    lines.append(f"风格：{style_keywords}")
    lines.append(f"避免项：{negative_keywords}")
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")

    for idx, scene in enumerate(scenes, start=1):
        num = f"{idx:02d}"
        prompt = build_single_prompt(destination, scene, style_keywords, negative_keywords, aspect_ratio)
        lines.append(f"## {num}.jpg {scene}")
        lines.append("")
        lines.append("```text")
        lines.append(prompt)
        lines.append("```")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 使用方式")
    lines.append("")
    lines.append("1. 复制上面每一条提示词到图片生成工具。")
    lines.append(f"2. 统一选择 {aspect_ratio} 画幅。")
    lines.append("3. 生成图片后按顺序命名为 01.jpg、02.jpg、03.jpg ...")
    lines.append("4. 放入项目的 images/ 文件夹。")
    lines.append("5. 回到本项目网页继续生成旅游伪视频。")
    lines.append("")
    return "\n".join(lines)


def build_view_data(prompt_output=None, prompt_form=None):
    ensure_dirs()
    data = load_script()
    image_files = sorted([p.name for p in IMAGE_DIR.iterdir() if p.suffix.lower() in ALLOWED_IMAGE_EXTS])
    has_output = OUTPUT_FILE.exists()
    bgm_exists = (AUDIO_DIR / "bgm.mp3").exists()
    voice_exists = (AUDIO_DIR / "voice.mp3").exists()

    if prompt_output is None:
        prompt_output = read_prompt_output()
    if prompt_form is None:
        prompt_form = default_prompt_form()

    return {
        "data": data,
        "image_files": image_files,
        "has_output": has_output,
        "bgm_exists": bgm_exists,
        "voice_exists": voice_exists,
        "prompt_output": prompt_output,
        "prompt_form": prompt_form
    }


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", **build_view_data())


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


@app.route("/delete_image", methods=["POST"])
def delete_image():
    ensure_dirs()
    filename = request.form.get("filename", "")
    image_path = image_path_from_name(filename)

    if image_path and image_path.exists():
        image_path.unlink()
        flash(f"已删除图片：{image_path.name}")
    else:
        flash("删除失败：没有找到这张图片。")

    return redirect(url_for("index"))


@app.route("/clear_images", methods=["POST"])
def clear_images():
    ensure_dirs()
    deleted = 0

    for image_file in IMAGE_DIR.iterdir():
        if image_file.suffix.lower() in ALLOWED_IMAGE_EXTS:
            image_file.unlink()
            deleted += 1

    flash(f"已清空图片，共删除 {deleted} 张。")
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


@app.route("/generate_prompts", methods=["POST"])
def generate_prompts():
    ensure_dirs()
    destination = request.form.get("destination", "泉州古城").strip() or "泉州古城"
    aspect_ratio = request.form.get("aspect_ratio", "16:9").strip() or "16:9"
    style_keywords = request.form.get("style_keywords", default_prompt_form()["style_keywords"]).strip()
    negative_keywords = request.form.get("negative_keywords", default_prompt_form()["negative_keywords"]).strip()
    scenes_text = request.form.get("scenes_text", "").strip()

    scenes = [line.strip() for line in scenes_text.splitlines() if line.strip()]
    if not scenes:
        scenes = [line.strip() for line in default_prompt_form()["scenes_text"].splitlines() if line.strip()]

    prompt_output = build_prompt_document(
        destination=destination,
        aspect_ratio=aspect_ratio,
        style_keywords=style_keywords,
        negative_keywords=negative_keywords,
        scenes=scenes
    )
    PROMPTS_FILE.write_text(prompt_output, encoding="utf-8")

    prompt_form = {
        "destination": destination,
        "aspect_ratio": aspect_ratio,
        "style_keywords": style_keywords,
        "negative_keywords": negative_keywords,
        "scenes_text": "\n".join(scenes)
    }

    flash("图片提示词已生成，并保存到 IMAGE_PROMPTS.md。")
    return render_template("index.html", **build_view_data(prompt_output=prompt_output, prompt_form=prompt_form))


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
            error_text = (result.stderr or result.stdout or "未知错误")[-1500:]
            flash("生成失败：\n" + error_text)
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
