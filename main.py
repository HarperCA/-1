from pathlib import Path
import asyncio
import json
import numpy as np
from PIL import Image
from moviepy.editor import (
    VideoClip,
    AudioFileClip,
    CompositeAudioClip,
    concatenate_audioclips,
    concatenate_videoclips,
)
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
VOICE_SEGMENT_DIR = AUDIO_DIR / "voice_segments"

# 横屏 16:9
VIDEO_W = 1920
VIDEO_H = 1080
FPS = 30

# 当前策略：不显示画面字幕，但生成 AI 旁白，并让每张图片时长尽量匹配对应旁白。
SHOW_SUBTITLES = False
ENABLE_AUDIO = True
VOICE_NAME = "zh-CN-XiaoxiaoNeural"
VOICE_RATE = "-8%"
VOICE_VOLUME = "+0%"
MIN_IMAGE_DURATION = 3.5
VOICE_PADDING = 0.45


def ensure_dirs():
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    VOICE_SEGMENT_DIR.mkdir(parents=True, exist_ok=True)


def load_script():
    """读取 script.json。不存在时自动创建默认脚本。"""
    if not SCRIPT_FILE.exists():
        default_voiceover = (
            "有些城市，不适合匆匆走过。\n"
            "泉州古城，更适合在清晨的光里慢慢靠近。\n"
            "老街、石板路和红砖屋檐，把时间留在了转角处。\n"
            "开元寺双塔静静伫立，像是在守望一座城的从前与现在。\n"
            "街边小店亮起温暖的灯，日常也有了旅行的味道。\n"
            "簪花背影走进古巷，传统在阳光里变得柔软。\n"
            "旅人穿过安静的巷子，也把脚步放慢了一点。\n"
            "当傍晚落下，泉州古城留给人的，是一种慢慢生活的感觉。"
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


def split_voiceover_lines(data, image_count):
    """把旁白拆成多句，尽量做到一张图对应一句旁白。"""
    voiceover_text = data.get("voiceover", "").strip()
    subtitles = data.get("subtitles", [])

    if voiceover_text:
        lines = [line.strip() for line in voiceover_text.splitlines() if line.strip()]
    else:
        lines = [line.strip() for line in subtitles if line.strip()]

    if not lines:
        return []

    if len(lines) < image_count:
        lines.extend([""] * (image_count - len(lines)))

    if len(lines) > image_count:
        kept = lines[:image_count - 1]
        kept.append(" ".join(lines[image_count - 1:]))
        lines = kept

    return lines


async def generate_voice_segments(lines):
    """逐句生成旁白音频，方便让每张图片时长匹配对应句子。"""
    if edge_tts is None:
        raise ImportError("缺少 edge-tts，请先运行：pip install -r requirements.txt")

    for old_file in VOICE_SEGMENT_DIR.glob("*.mp3"):
        old_file.unlink()

    segment_paths = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            segment_paths.append(None)
            continue

        segment_path = VOICE_SEGMENT_DIR / f"voice_{index:02d}.mp3"
        print(f"正在生成第 {index} 段旁白：{line}")
        communicate = edge_tts.Communicate(
            text=line,
            voice=VOICE_NAME,
            rate=VOICE_RATE,
            volume=VOICE_VOLUME,
        )
        await communicate.save(str(segment_path))
        segment_paths.append(segment_path)

    return segment_paths


def get_audio_durations(segment_paths, fallback_duration):
    """读取每段旁白时长，并给每张图留一点停顿。"""
    durations = []
    audio_clips = []

    for segment_path in segment_paths:
        if segment_path and Path(segment_path).exists():
            audio_clip = AudioFileClip(str(segment_path))
            audio_clips.append(audio_clip)
            durations.append(max(MIN_IMAGE_DURATION, audio_clip.duration + VOICE_PADDING))
        else:
            audio_clips.append(None)
            durations.append(fallback_duration)

    return durations, audio_clips


def make_clip(image_path, duration, mode):
    """把单张图片做成带运镜的视频片段。"""
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


def build_aligned_voice_track(audio_clips, durations):
    """把每句旁白按对应图片时长拼接，保证换图和换句尽量同步。"""
    segment_timeline = []

    for audio_clip, duration in zip(audio_clips, durations):
        if audio_clip is None:
            continue
        audio_clip = audio_clip.volumex(1.1).set_start(sum(durations[:len(segment_timeline)]))
        segment_timeline.append(audio_clip)

    if not segment_timeline:
        return None

    # 用 CompositeAudioClip 保留每段之间的自然空白。
    return CompositeAudioClip(segment_timeline).set_duration(sum(durations))


def build_bgm_track(video_duration, has_voice):
    bgm_path = AUDIO_DIR / "bgm.mp3"
    if not bgm_path.exists():
        return None

    print("正在添加背景音乐...")
    bgm = AudioFileClip(str(bgm_path))
    if bgm.duration < video_duration:
        bgm = audio_loop(bgm, duration=video_duration)
    else:
        bgm = bgm.subclip(0, video_duration)

    bgm_volume = 0.14 if has_voice else 0.28
    bgm = bgm.volumex(bgm_volume)
    bgm = audio_fadein(bgm, 1.2)
    bgm = audio_fadeout(bgm, 2.0)
    return bgm.set_duration(video_duration)


def main():
    ensure_dirs()

    data = load_script()
    fallback_duration = int(data.get("duration_per_image", 6))

    image_files = sorted([
        p for p in IMAGE_DIR.iterdir()
        if p.suffix.lower() in [".jpg", ".jpeg", ".png"]
    ])

    if not image_files:
        raise FileNotFoundError(
            "images 文件夹里没有图片。\n"
            "请先通过网页上传图片，或者手动放入 01.jpg 到 08.jpg。"
        )

    print(f"发现 {len(image_files)} 张图片。")
    print(f"视频尺寸：{VIDEO_W}x{VIDEO_H}，横屏 16:9。")

    voice_lines = split_voiceover_lines(data, len(image_files))
    segment_paths = []
    audio_clips = []
    durations = [fallback_duration] * len(image_files)

    if ENABLE_AUDIO and voice_lines:
        try:
            segment_paths = asyncio.run(generate_voice_segments(voice_lines))
            durations, audio_clips = get_audio_durations(segment_paths, fallback_duration)
            valid_segments = [p for p in segment_paths if p]
            if valid_segments:
                joined_audio = concatenate_audioclips([AudioFileClip(str(p)) for p in valid_segments])
                joined_audio.write_audiofile(str(VOICE_FILE), fps=44100)
            print("已根据每句旁白时长自动调整每张图片的停留时间。")
        except Exception as exc:
            print(f"AI 旁白生成失败，将使用默认每张图片 {fallback_duration} 秒。原因：{exc}")
            durations = [fallback_duration] * len(image_files)
            audio_clips = []

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
        duration = durations[index]
        print(f"正在处理：{image_path.name}，运镜：{mode}，时长：{duration:.2f} 秒")
        clips.append(make_clip(image_path, duration, mode))

    final_video = concatenate_videoclips(clips, method="compose")
    video_duration = final_video.duration

    audio_tracks = []
    voice_track = build_aligned_voice_track(audio_clips, durations) if audio_clips else None
    if voice_track:
        audio_tracks.append(voice_track)

    bgm_track = build_bgm_track(video_duration, has_voice=voice_track is not None)
    if bgm_track:
        audio_tracks.append(bgm_track)

    if audio_tracks:
        final_video = final_video.set_audio(CompositeAudioClip(audio_tracks).set_duration(video_duration))

    print("正在导出视频...")
    final_video.write_videofile(
        str(OUTPUT_FILE),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        bitrate="6000k",
        threads=4,
        ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    )

    print(f"完成：{OUTPUT_FILE}")


if __name__ == "__main__":
    main()
