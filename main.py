from pathlib import Path
import asyncio
import json
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import VideoClip, concatenate_videoclips, AudioFileClip, CompositeAudioClip

try:
    import edge_tts
except ImportError:
    edge_tts = None

BASE_DIR = Path(__file__).parent
IMAGE_DIR = BASE_DIR / "images"
AUDIO_DIR = BASE_DIR / "audio"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_FILE = OUTPUT_DIR / "final.mp4"
SCRIPT_FILE = BASE_DIR / "script.json"
VOICE_FILE = AUDIO_DIR / "voice.mp3"

# 竖屏：1080 x 1920；横屏 16:9 可改成 1920 x 1080
VIDEO_W = 1080
VIDEO_H = 1920
FPS = 30

# edge-tts 中文声音，可改成 zh-CN-YunxiNeural 男声
VOICE_NAME = "zh-CN-XiaoxiaoNeural"


def get_font(size=54):
    """Load a Chinese-capable font on Windows."""
    font_paths = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
    ]
    for path in font_paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def resize_cover(img, target_w, target_h):
    """Resize and crop image to fill target size without black borders."""
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


def add_subtitle(frame, text):
    """Draw centered white Chinese subtitles near the bottom."""
    img = frame.copy()
    draw = ImageDraw.Draw(img)
    font = get_font(54)
    max_width = int(VIDEO_W * 0.82)

    lines = []
    current = ""
    for char in text:
        test = current + char
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = char
    if current:
        lines.append(current)

    line_height = 72
    total_height = len(lines) * line_height
    y = VIDEO_H - 300 - total_height // 2

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = (VIDEO_W - text_w) // 2

        for dx, dy in [(-3, -3), (3, -3), (-3, 3), (3, 3)]:
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0))

        draw.text((x, y), line, font=font, fill=(255, 255, 255))
        y += line_height

    return img


def make_clip(image_path, subtitle, duration, mode):
    """Turn one still image into a moving Ken Burns style video clip."""
    original = Image.open(image_path).convert("RGB")
    base = resize_cover(original, VIDEO_W, VIDEO_H)

    def make_frame(t):
        progress = t / duration

        if mode == "zoom_in":
            scale, dx, dy = 1.0 + 0.10 * progress, 0, 0
        elif mode == "zoom_out":
            scale, dx, dy = 1.10 - 0.10 * progress, 0, 0
        elif mode == "pan_left":
            scale, dx, dy = 1.10, int(80 * (0.5 - progress)), 0
        elif mode == "pan_right":
            scale, dx, dy = 1.10, int(80 * (progress - 0.5)), 0
        elif mode == "pan_up":
            scale, dx, dy = 1.10, 0, int(80 * (0.5 - progress))
        elif mode == "pan_down":
            scale, dx, dy = 1.10, 0, int(80 * (progress - 0.5))
        else:
            scale, dx, dy = 1.0, 0, 0

        new_w = int(VIDEO_W * scale)
        new_h = int(VIDEO_H * scale)
        frame = base.resize((new_w, new_h), Image.LANCZOS)

        left = (new_w - VIDEO_W) // 2 + dx
        top = (new_h - VIDEO_H) // 2 + dy
        left = max(0, min(left, new_w - VIDEO_W))
        top = max(0, min(top, new_h - VIDEO_H))

        frame = frame.crop((left, top, left + VIDEO_W, top + VIDEO_H))
        frame = add_subtitle(frame, subtitle)
        return np.array(frame)

    return VideoClip(make_frame, duration=duration).set_fps(FPS)


async def generate_voiceover(text):
    """Generate AI voiceover with edge-tts."""
    if edge_tts is None:
        raise ImportError("缺少 edge-tts，请先运行：pip install -r requirements.txt")

    if not text.strip():
        return None

    print("正在生成 AI 旁白配音...")
    communicate = edge_tts.Communicate(
        text=text,
        voice=VOICE_NAME,
        rate="-8%",
        volume="+0%"
    )
    await communicate.save(str(VOICE_FILE))
    print(f"AI 旁白已生成：{VOICE_FILE}")
    return VOICE_FILE


def build_audio_track(final_video, voice_path=None):
    """Combine voiceover and optional background music."""
    audio_clips = []

    if voice_path and Path(voice_path).exists():
        voice = AudioFileClip(str(voice_path))
        if voice.duration > final_video.duration:
            voice = voice.subclip(0, final_video.duration)
        audio_clips.append(voice.volumex(1.0))

    bgm_path = AUDIO_DIR / "bgm.mp3"
    if bgm_path.exists():
        print("正在添加背景音乐...")
        bgm = AudioFileClip(str(bgm_path))
        if bgm.duration < final_video.duration:
            bgm = bgm.loop(duration=final_video.duration)
        else:
            bgm = bgm.subclip(0, final_video.duration)
        audio_clips.append(bgm.volumex(0.18 if voice_path else 0.25))

    if audio_clips:
        return CompositeAudioClip(audio_clips)
    return None


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    data = json.loads(SCRIPT_FILE.read_text(encoding="utf-8"))
    image_duration = int(data.get("duration_per_image", 6))
    subtitles = data.get("subtitles", [])
    voiceover_text = data.get("voiceover") or "\n".join(subtitles)

    image_files = sorted([
        p for p in IMAGE_DIR.iterdir()
        if p.suffix.lower() in [".jpg", ".jpeg", ".png"]
    ])
    if not image_files:
        raise FileNotFoundError("images 文件夹里没有图片。请先放入 01.jpg 到 08.jpg，或运行 python scripts/generate_demo_images.py 生成示例图。")

    modes = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_up", "zoom_in", "pan_down", "zoom_out"]
    clips = []

    for index, image_path in enumerate(image_files):
        subtitle = subtitles[index] if index < len(subtitles) else ""
        mode = modes[index % len(modes)]
        print(f"正在处理：{image_path.name}，运镜：{mode}")
        clips.append(make_clip(image_path, subtitle, image_duration, mode))

    final_video = concatenate_videoclips(clips, method="compose")

    voice_path = None
    try:
        voice_path = asyncio.run(generate_voiceover(voiceover_text))
    except Exception as exc:
        print(f"AI 旁白生成失败，将继续导出无旁白视频。原因：{exc}")

    audio = build_audio_track(final_video, voice_path)
    if audio:
        final_video = final_video.set_audio(audio)

    print("正在导出视频...")
    final_video.write_videofile(
        str(OUTPUT_FILE),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        bitrate="6000k"
    )
    print(f"完成：{OUTPUT_FILE}")


if __name__ == "__main__":
    main()
