from pathlib import Path
import json
import numpy as np
from PIL import Image
from moviepy.editor import VideoClip, concatenate_videoclips


BASE_DIR = Path(__file__).parent
IMAGE_DIR = BASE_DIR / "images"
AUDIO_DIR = BASE_DIR / "audio"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_FILE = OUTPUT_DIR / "final.mp4"
SCRIPT_FILE = BASE_DIR / "script.json"

# 横屏 16:9
VIDEO_W = 1920
VIDEO_H = 1080
FPS = 30

# 当前需求：最终视频不要画面字幕，也不要任何声音。
# voiceover 只作为旁白文案保存，不会生成音频。
SHOW_SUBTITLES = False
ENABLE_AUDIO = False


def ensure_dirs():
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_script():
    """读取 script.json。不存在时自动创建默认脚本。"""
    if not SCRIPT_FILE.exists():
        default_voiceover = (
            "有些城市，不适合匆匆路过。\n"
            "泉州古城，更适合慢慢走，慢慢看。\n"
            "红砖古厝、老巷屋檐，藏着闽南的时间。\n"
            "开元寺双塔静静伫立，见证一座城的千年烟火。\n"
            "街边小店、热汤升腾，是最真实的人间日常。\n"
            "簪花从古巷走过，把传统留在温柔的光里。\n"
            "旅行有时候不是为了去很远。\n"
            "而是在一座城里，重新感受到生活。"
        )
        default_data = {
            "title": "慢慢抵达泉州古城",
            "duration_per_image": 6,
            "aspect_ratio": "16:9",
            "subtitles": [],
            "voiceover": default_voiceover
        }
        SCRIPT_FILE.write_text(
            json.dumps(default_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    return json.loads(SCRIPT_FILE.read_text(encoding="utf-8"))


def resize_cover(img, target_w, target_h):
    """把图片等比例放大并裁剪成目标尺寸，避免黑边。"""
    img = img.convert("RGB")
    src_w, src_h = img.size
    src_ratio = src_w / src_h
    target_ratio = target_w / target_h

    if src_ratio > target_ratio:
        new_h = target_h
        new_w = int(src_w * target_h / src_h)
    else:
        new_w = target_w
        new_h = int(src_h * target_w / src_w)

    img = img.resize((new_w, new_h), Image.LANCZOS)

    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2

    return img.crop((left, top, left + target_w, top + target_h))


def make_clip(image_path, duration, mode):
    """把单张图片做成无字幕、无声音、带运镜的视频片段。"""
    original = Image.open(image_path).convert("RGB")
    base = resize_cover(original, VIDEO_W, VIDEO_H)

    def make_frame(t):
        progress = max(0, min(t / duration, 1))

        if mode == "zoom_in":
            scale = 1.00 + 0.10 * progress
            dx, dy = 0, 0
        elif mode == "zoom_out":
            scale = 1.10 - 0.10 * progress
            dx, dy = 0, 0
        elif mode == "pan_left":
            scale = 1.10
            dx = int(120 * (0.5 - progress))
            dy = 0
        elif mode == "pan_right":
            scale = 1.10
            dx = int(120 * (progress - 0.5))
            dy = 0
        elif mode == "pan_up":
            scale = 1.10
            dx = 0
            dy = int(90 * (0.5 - progress))
        elif mode == "pan_down":
            scale = 1.10
            dx = 0
            dy = int(90 * (progress - 0.5))
        else:
            scale = 1.04
            dx, dy = 0, 0

        new_w = int(VIDEO_W * scale)
        new_h = int(VIDEO_H * scale)
        frame = base.resize((new_w, new_h), Image.LANCZOS)

        left = (new_w - VIDEO_W) // 2 + dx
        top = (new_h - VIDEO_H) // 2 + dy
        left = max(0, min(left, new_w - VIDEO_W))
        top = max(0, min(top, new_h - VIDEO_H))

        frame = frame.crop((left, top, left + VIDEO_W, top + VIDEO_H))
        return np.array(frame)

    return VideoClip(make_frame, duration=duration).set_fps(FPS)


def main():
    ensure_dirs()

    data = load_script()
    image_duration = int(data.get("duration_per_image", 6))

    # 保留旁白文本字段，但当前不会生成 voice.mp3，也不会写入视频音轨。
    voiceover_text = data.get("voiceover", "").strip()
    if voiceover_text:
        print("已读取旁白文本：仅作为文案保存，不生成声音。")

    image_files = sorted([
        p for p in IMAGE_DIR.iterdir()
        if p.suffix.lower() in [".jpg", ".jpeg", ".png"]
    ])

    if not image_files:
        raise FileNotFoundError(
            "images 文件夹里没有图片。\n"
            "请先放入 01.jpg 到 08.jpg，或者运行：python scripts/generate_demo_images.py"
        )

    print(f"发现 {len(image_files)} 张图片。")
    print(f"视频尺寸：{VIDEO_W}x{VIDEO_H}，横屏 16:9。")
    print("当前输出设置：无画面字幕、无声音。")

    modes = [
        "zoom_in",
        "pan_right",
        "zoom_out",
        "pan_left",
        "pan_up",
        "zoom_in",
        "pan_down",
        "zoom_out"
    ]

    clips = []
    for index, image_path in enumerate(image_files):
        mode = modes[index % len(modes)]
        print(f"正在处理：{image_path.name}，运镜：{mode}")
        clips.append(make_clip(image_path, image_duration, mode))

    final_video = concatenate_videoclips(clips, method="compose")

    print("正在导出无声视频...")
    final_video.write_videofile(
        str(OUTPUT_FILE),
        fps=FPS,
        codec="libx264",
        audio=False,
        bitrate="6000k",
        threads=4
    )

    print(f"完成：{OUTPUT_FILE}")


if __name__ == "__main__":
    main()
