from pathlib import Path
import asyncio
import json
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import VideoClip, concatenate_videoclips, AudioFileClip, CompositeAudioClip
from moviepy.audio.fx.all import audio_loop, audio_fadein, audio_fadeout

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

# 横屏 16:9
VIDEO_W = 1920
VIDEO_H = 1080
FPS = 30

# edge-tts 中文声音
# 女声：zh-CN-XiaoxiaoNeural
# 男声：zh-CN-YunxiNeural
VOICE_NAME = "zh-CN-XiaoxiaoNeural"

FONT_CACHE = {}


def ensure_dirs():
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_font(size=54):
    """加载中文字体，优先使用 Windows 常见中文字体。"""
    if size in FONT_CACHE:
        return FONT_CACHE[size]

    font_paths = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
    ]

    for path in font_paths:
        if Path(path).exists():
            FONT_CACHE[size] = ImageFont.truetype(path, size)
            return FONT_CACHE[size]

    FONT_CACHE[size] = ImageFont.load_default()
    return FONT_CACHE[size]


def load_script():
    """读取 script.json。不存在时自动创建默认脚本。"""
    if not SCRIPT_FILE.exists():
        default_data = {
            "title": "慢慢抵达泉州古城",
            "duration_per_image": 6,
            "aspect_ratio": "16:9",
            "subtitles": [
                "有些城市，不适合匆匆路过。",
                "泉州古城，更适合慢慢走，慢慢看。",
                "红砖古厝、老巷屋檐，藏着闽南的时间。",
                "开元寺双塔静静伫立，见证一座城的千年烟火。",
                "街边小店、热汤升腾，是最真实的人间日常。",
                "簪花从古巷走过，把传统留在温柔的光里。",
                "旅行有时候不是为了去很远。",
                "而是在一座城里，重新感受到生活。"
            ],
            "voiceover": ""
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


def split_text_lines(draw, text, font, max_width):
    """把中文字幕按宽度自动换行。"""
    lines = []
    current = ""

    for char in text:
        test = current + char
        bbox = draw.textbbox((0, 0), test, font=font)
        test_w = bbox[2] - bbox[0]

        if test_w <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = char

    if current:
        lines.append(current)

    return lines


def make_subtitle_layer(text):
    """预先生成字幕透明图层，避免每一帧重复排版，提高导出速度。"""
    if not text:
        return None

    layer = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    font = get_font(54)
    max_width = int(VIDEO_W * 0.82)
    lines = split_text_lines(draw, text, font, max_width)

    line_height = 72
    padding_x = 38
    padding_y = 24
    total_text_height = len(lines) * line_height
    box_h = total_text_height + padding_y * 2

    # 字幕区域放在底部偏上，避免太贴边。
    box_y = VIDEO_H - 210 - box_h // 2
    box_y = max(0, box_y)

    box_w = int(VIDEO_W * 0.78)
    box_x = (VIDEO_W - box_w) // 2

    draw.rounded_rectangle(
        (box_x, box_y, box_x + box_w, box_y + box_h),
        radius=28,
        fill=(0, 0, 0, 105)
    )

    y = box_y + padding_y
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = (VIDEO_W - text_w) // 2

        # 黑色描边
        for dx, dy in [(-3, 0), (3, 0), (0, -3), (0, 3)]:
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 220))

        # 白色正文
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_height

    return layer


def make_clip(image_path, subtitle, duration, mode):
    """把单张图片做成带运镜的视频片段。"""
    original = Image.open(image_path).convert("RGB")
    base = resize_cover(original, VIDEO_W, VIDEO_H)
    subtitle_layer = make_subtitle_layer(subtitle)

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

        if subtitle_layer:
            frame = Image.alpha_composite(frame.convert("RGBA"), subtitle_layer).convert("RGB")

        return np.array(frame)

    return VideoClip(make_frame, duration=duration).set_fps(FPS)


async def generate_voiceover(text):
    """用 edge-tts 生成中文 AI 旁白。"""
    if edge_tts is None:
        raise ImportError("缺少 edge-tts，请先运行：pip install -r requirements.txt")

    if not text or not text.strip():
        return None

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

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
    """
    混合 AI 旁白和背景音乐。

    规则：
    1. 有旁白时，背景音乐压低。
    2. 背景音乐短于视频时自动循环。
    3. 背景音乐开头淡入、结尾淡出。
    """
    audio_clips = []
    video_duration = final_video.duration

    if voice_path and Path(voice_path).exists():
        print("正在添加 AI 旁白...")
        voice = AudioFileClip(str(voice_path))

        if voice.duration > video_duration:
            voice = voice.subclip(0, video_duration)

        voice = voice.volumex(1.15)
        audio_clips.append(voice)

    bgm_path = AUDIO_DIR / "bgm.mp3"
    if bgm_path.exists():
        print("正在添加背景音乐...")
        bgm = AudioFileClip(str(bgm_path))

        if bgm.duration < video_duration:
            bgm = audio_loop(bgm, duration=video_duration)
        else:
            bgm = bgm.subclip(0, video_duration)

        bgm_volume = 0.16 if voice_path else 0.28
        bgm = bgm.volumex(bgm_volume)
        bgm = audio_fadein(bgm, 1.2)
        bgm = audio_fadeout(bgm, 2.0)

        audio_clips.append(bgm)

    if audio_clips:
        return CompositeAudioClip(audio_clips).set_duration(video_duration)

    return None


def main():
    ensure_dirs()

    data = load_script()
    image_duration = int(data.get("duration_per_image", 6))
    subtitles = data.get("subtitles", [])
    voiceover_text = data.get("voiceover") or "\n".join(subtitles)

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
        bitrate="6000k",
        threads=4
    )

    print(f"完成：{OUTPUT_FILE}")


if __name__ == "__main__":
    main()
