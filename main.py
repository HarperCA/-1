from pathlib import Path
import asyncio
import concurrent.futures
from functools import lru_cache
import json
import math
import os
import re
import subprocess
import tempfile
import time

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
from external_video_engines import (
    normalize_engine_name,
    render_with_3d_ken_burns,
    render_with_kburns_slideshow,
    should_use_external_engine,
)

try:
    from moviepy.editor import AudioFileClip, CompositeAudioClip, VideoClip, concatenate_audioclips, concatenate_videoclips
except ModuleNotFoundError:
    from moviepy import AudioFileClip, CompositeAudioClip, VideoClip, concatenate_audioclips, concatenate_videoclips


BASE_DIR = Path(__file__).parent
IMAGE_DIR = BASE_DIR / "images"
AUDIO_DIR = BASE_DIR / "audio"
OUTPUT_DIR = BASE_DIR / "output"
SCRIPT_FILE = BASE_DIR / "script.json"
OUTPUT_FILE = OUTPUT_DIR / "final.mp4"
RENDERING_OUTPUT_FILE = OUTPUT_DIR / "final.rendering.mp4"
DEBUG_FIRST_FRAME = OUTPUT_DIR / "debug_first_frame.jpg"
QUALITY_CONTACT_SHEET = OUTPUT_DIR / "quality_contact_sheet.jpg"
QUALITY_REPORT_FILE = OUTPUT_DIR / "quality_report.json"
BGM_FILE = AUDIO_DIR / "bgm.mp3"
VOICE_FILE = OUTPUT_DIR / "voice.mp3"
VOICE_FALLBACK_FILE = OUTPUT_DIR / "voice.wav"

VIDEO_W = int(os.getenv("VIDEO_WIDTH", "1920"))
VIDEO_H = int(os.getenv("VIDEO_HEIGHT", "1080"))
VIDEO_ENFORCE_16_9 = os.getenv("VIDEO_ENFORCE_16_9", "1").strip().lower() not in {"0", "false", "no", "off"}
if VIDEO_ENFORCE_16_9 and abs((VIDEO_W / VIDEO_H) - (16 / 9)) > 0.001:
    VIDEO_H = int(round(VIDEO_W * 9 / 16))
    if VIDEO_H % 2:
        VIDEO_H += 1
FPS = int(os.getenv("VIDEO_FPS", "30"))
VIDEO_CRF = os.getenv("VIDEO_CRF", "17")
VIDEO_PRESET = os.getenv("VIDEO_PRESET", "slow")
VIDEO_BITRATE = os.getenv("VIDEO_BITRATE", "").strip()
AUDIO_BITRATE = os.getenv("AUDIO_BITRATE", "192k")
BGM_VOLUME = float(os.getenv("BGM_VOLUME", "0.12"))
VOICE_VOLUME = float(os.getenv("VOICE_VOLUME", "1.08"))
DEFAULT_VIDEO_THREADS = max(4, (os.cpu_count() or 4) - 1)
VIDEO_LAYOUT_MODE = os.getenv("VIDEO_LAYOUT_MODE", "cinematic").strip().lower()
VIDEO_MAX_SHOT_DURATION = float(os.getenv("VIDEO_MAX_SHOT_DURATION", "3.35"))
VIDEO_MIN_SHOT_DURATION = float(os.getenv("VIDEO_MIN_SHOT_DURATION", "2.35"))
VIDEO_MIN_VOICE_SHOT_DURATION = float(os.getenv("VIDEO_MIN_VOICE_SHOT_DURATION", "0.85"))
VIDEO_TRANSITION_STYLE = os.getenv("VIDEO_TRANSITION_STYLE", "cut").strip().lower()
VIDEO_CULTURE_LABELS = os.getenv("VIDEO_CULTURE_LABELS", "1").strip().lower() not in {"0", "false", "no", "off"}
VOICEOVER_RATE = os.getenv("VOICEOVER_RATE", "+18%")
VOICEOVER_WORKERS = max(1, int(os.getenv("VOICEOVER_WORKERS", "4")))
VOICEOVER_TAIL_PADDING = float(os.getenv("VOICEOVER_TAIL_PADDING", "0.22"))
SUBTITLE_SECONDS_PER_CHAR = float(os.getenv("SUBTITLE_SECONDS_PER_CHAR", "0.16"))
VIDEO_SAVE_DEBUG_FIRST_FRAME = os.getenv("VIDEO_SAVE_DEBUG_FIRST_FRAME", "0").strip().lower() not in {"0", "false", "no", "off"}
VIDEO_SAVE_QUALITY_REPORT = os.getenv("VIDEO_SAVE_QUALITY_REPORT", "1").strip().lower() not in {"0", "false", "no", "off"}
DEFAULT_DURATION = 3.2
TITLE_DURATION = 2.1
OUTRO_DURATION = 1.7
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
RESAMPLE_HIGH = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
RESAMPLE_BICUBIC = Image.Resampling.BICUBIC if hasattr(Image, "Resampling") else Image.BICUBIC
_MOVING_COVER_CACHE = {}
_BACKGROUND_CACHE = {}
_SUBTITLE_OVERLAY_CACHE = {}
_CINEMATIC_MASK_CACHE = None

LAYOUTS = [
    "establishing",
    "split_route",
    "diagonal_cut",
    "detail_stack",
    "triptych",
    "postcard",
]


def ensure_dirs():
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def promote_rendered_output(rendered_path):
    if rendered_path == OUTPUT_FILE:
        return
    if OUTPUT_FILE.exists():
        OUTPUT_FILE.unlink()
    rendered_path.replace(OUTPUT_FILE)


def load_script_data():
    if not SCRIPT_FILE.exists():
        return {}

    try:
        data = json.loads(SCRIPT_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def load_duration_per_image(script=None):
    script = script if script is not None else load_script_data()
    try:
        duration = float(script.get("duration_per_image", DEFAULT_DURATION))
    except (TypeError, ValueError):
        duration = DEFAULT_DURATION

    duration = max(VIDEO_MIN_SHOT_DURATION, duration)
    return min(duration, VIDEO_MAX_SHOT_DURATION)


def quality_env_enabled(name, default=True):
    value = os.getenv(name, "1" if default else "0").strip().lower()
    return value not in {"0", "false", "no", "off"}


def fit_text_lines(lines, count, fallback_prefix, repeat_existing=False):
    lines = [str(line).strip() for line in (lines or []) if str(line).strip()]
    if not lines:
        lines = [f"{fallback_prefix}{index}" for index in range(1, count + 1)]
    source_lines = list(lines)
    while len(lines) < count:
        if repeat_existing and source_lines:
            lines.append(source_lines[len(lines) % len(source_lines)])
        else:
            lines.append(f"{fallback_prefix}{len(lines) + 1}")
    return lines[:count]


def load_subtitles(script, count):
    subtitles = script.get("subtitles") or []
    if isinstance(subtitles, str):
        subtitles = [line.strip() for line in subtitles.splitlines()]
    return fit_text_lines(subtitles, count, "镜头", repeat_existing=True)


def load_voiceover(script):
    voiceover = script.get("voiceover") or ""
    if isinstance(voiceover, list):
        voiceover = "\n".join(str(line).strip() for line in voiceover if str(line).strip())
    return str(voiceover).strip()


def load_voiceover_lines(script, count, subtitles=None):
    voiceover = script.get("voiceover") or []
    if isinstance(voiceover, str):
        voiceover = [line.strip() for line in voiceover.splitlines()]
    subtitles = subtitles or []
    fallback = [line if str(line).endswith(("。", "！", "？", ".", "!", "?")) else f"{line}。" for line in subtitles]
    return fit_text_lines(voiceover, count, "镜头", repeat_existing=True) if voiceover else fit_text_lines(fallback, count, "镜头", repeat_existing=True)


CULTURE_LABELS = [
    ("水巷", "枕河人家"),
    ("园林", "移步换景"),
    ("石桥", "烟火过岸"),
    ("白墙黛瓦", "江南底色"),
    ("荷塘", "风起清圆"),
    ("松影", "留白成诗"),
    ("古城", "慢入人间"),
    ("晨雾", "一城初醒"),
]


def culture_label_for(index, subtitle=""):
    text = repair_text(subtitle)
    for keyword, label, note in [
        ("荷", "荷塘", "风起清圆"),
        ("桥", "石桥", "烟火过岸"),
        ("巷", "水巷", "枕河人家"),
        ("园", "园林", "移步换景"),
        ("墙", "白墙黛瓦", "江南底色"),
        ("松", "松影", "留白成诗"),
        ("晨", "晨雾", "一城初醒"),
    ]:
        if keyword in text:
            return label, note
    return CULTURE_LABELS[index % len(CULTURE_LABELS)]


def add_cultural_marker(frame, index, progress, subtitle=""):
    if not VIDEO_CULTURE_LABELS or index not in {1, 3, 5, 7}:
        return frame

    appear = ease(clamp((progress - 0.08) / 0.16))
    leave = 1.0 - ease(clamp((progress - 0.54) / 0.18))
    alpha = int(168 * appear * leave)
    if alpha <= 0:
        return frame

    label, note = culture_label_for(index, subtitle)
    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    x = 92
    y = 96
    label_font = load_font(31)
    note_font = load_font(18)
    draw.line((x, y + 3, x, y + 70), fill=(226, 205, 160, alpha), width=2)
    draw.text((x + 18, y), label, font=label_font, fill=(255, 249, 230, alpha))
    draw.text((x + 20, y + 43), note, font=note_font, fill=(226, 216, 194, int(alpha * 0.82)))
    return Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")


def clean_title(script):
    title = str(script.get("title") or script.get("xiaohongshu_title") or "旅行慢视频").strip()
    title = title.replace("｜", " ").replace("|", " ")
    return title[:28]


def closing_line(title):
    place = title.replace("慢旅行", "").replace("旅行", "").strip() or "这段旅程"
    return f"把这份温柔，留在{place}。"


def get_image_files():
    ensure_dirs()

    def sort_key(path):
        return (0, int(path.stem)) if path.stem.isdigit() else (1, path.name.lower())

    return sorted(
        (
            image
            for image in IMAGE_DIR.iterdir()
            if image.is_file() and image.suffix.lower() in ALLOWED_IMAGE_EXTS
        ),
        key=sort_key,
    )


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def ease(progress):
    progress = clamp(progress)
    return progress * progress * (3 - 2 * progress)


def ease_out(progress):
    progress = clamp(progress)
    return 1 - (1 - progress) * (1 - progress)


def shot_duration_for(index, base_duration):
    accents = [0.86, 1.00, 0.78, 0.96, 0.74, 1.02, 0.80, 0.92]
    duration = float(base_duration) * accents[index % len(accents)]
    return max(VIDEO_MIN_SHOT_DURATION, min(VIDEO_MAX_SHOT_DURATION, duration))


def estimate_subtitle_duration(text, base_duration):
    text = str(text or "").strip()
    if not text:
        return shot_duration_for(0, base_duration)
    readable_chars = len([char for char in text if not char.isspace()])
    estimated = readable_chars * SUBTITLE_SECONDS_PER_CHAR + VOICEOVER_TAIL_PADDING
    return max(VIDEO_MIN_SHOT_DURATION, estimated)


def audio_duration(audio_path):
    if not audio_path:
        return None
    try:
        clip = AudioFileClip(str(audio_path))
        try:
            return float(clip.duration or 0)
        finally:
            clip.close()
    except Exception as exc:
        print(f"Could not read audio duration for {audio_path}: {exc}")

    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        result = subprocess.run(
            [
                ffmpeg,
                "-i",
                str(audio_path),
                "-f",
                "null",
                "-",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr or "")
        if match:
            hours, minutes, seconds = match.groups()
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except Exception as exc:
        print(f"Could not probe audio duration for {audio_path}: {exc}")
    return None


def shot_durations_for_timeline(voice_paths, subtitles, base_duration):
    durations = []
    for index, subtitle in enumerate(subtitles):
        duration = audio_duration(voice_paths[index]) if index < len(voice_paths) else None
        subtitle_duration = estimate_subtitle_duration(subtitle, base_duration)
        if duration and duration > 0:
            shot_duration = max(VIDEO_MIN_VOICE_SHOT_DURATION, duration + VOICEOVER_TAIL_PADDING, subtitle_duration)
            source = f"voice={duration:.2f}s"
        else:
            shot_duration = subtitle_duration
            source = "subtitle-estimate"
        durations.append(shot_duration)
        print(f"Shot {index + 1:02d} duration from {source}: {shot_duration:.2f}s")
    return durations


def resize_cover(image, target_w=VIDEO_W, target_h=VIDEO_H):
    image = image.convert("RGB")
    src_w, src_h = image.size
    src_ratio = src_w / src_h
    target_ratio = target_w / target_h

    if src_ratio > target_ratio:
        new_h = target_h
        new_w = int(src_w * target_h / src_h)
    else:
        new_w = target_w
        new_h = int(src_h * target_w / src_w)

    resized = image.resize((new_w, new_h), RESAMPLE_HIGH)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def resize_contain(image, target_w, target_h):
    image = image.convert("RGB")
    src_w, src_h = image.size
    scale = min(target_w / src_w, target_h / src_h)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    return image.resize((new_w, new_h), RESAMPLE_HIGH)


def moving_cover(image, target_w, target_h, progress, direction, zoom=0.12):
    progress = ease(progress)
    scale = 1.0 + zoom
    if direction in {"left", "right", "up", "down"}:
        cache_key = (id(image), target_w, target_h, direction, round(zoom, 4))
        src = _MOVING_COVER_CACHE.get(cache_key)
        if src is None:
            src = resize_cover(image, int(target_w * scale), int(target_h * scale))
            _MOVING_COVER_CACHE[cache_key] = src
    else:
        src = resize_cover(image, int(target_w * scale), int(target_h * scale))
    max_x = src.size[0] - target_w
    max_y = src.size[1] - target_h

    if direction == "left":
        x = int(max_x * (0.75 - 0.5 * progress))
        y = max_y // 2
    elif direction == "right":
        x = int(max_x * (0.25 + 0.5 * progress))
        y = max_y // 2
    elif direction == "up":
        x = max_x // 2
        y = int(max_y * (0.75 - 0.5 * progress))
    elif direction == "down":
        x = max_x // 2
        y = int(max_y * (0.25 + 0.5 * progress))
    elif direction == "out":
        scale = 1.14 - 0.12 * progress
        src = resize_cover(image, int(target_w * scale), int(target_h * scale))
        x = (src.size[0] - target_w) // 2
        y = (src.size[1] - target_h) // 2
    else:
        scale = 1.0 + zoom * progress
        src = resize_cover(image, int(target_w * scale), int(target_h * scale))
        x = (src.size[0] - target_w) // 2
        y = (src.size[1] - target_h) // 2

    x = max(0, min(x, src.size[0] - target_w))
    y = max(0, min(y, src.size[1] - target_h))
    return src.crop((x, y, x + target_w, y + target_h))


def background_from(image):
    cache_key = id(image)
    cached = _BACKGROUND_CACHE.get(cache_key)
    if cached is not None:
        return cached.copy()

    bg = resize_cover(image, VIDEO_W, VIDEO_H)
    bg = bg.filter(ImageFilter.GaussianBlur(14))
    bg = ImageEnhance.Color(bg).enhance(0.82)
    overlay = Image.new("RGB", (VIDEO_W, VIDEO_H), (16, 18, 22))
    result = Image.blend(bg, overlay, 0.28)
    _BACKGROUND_CACHE[cache_key] = result
    return result.copy()


def add_letterbox(frame, size=58):
    draw = ImageDraw.Draw(frame)
    draw.rectangle((0, 0, VIDEO_W, size), fill=(8, 10, 14))
    draw.rectangle((0, VIDEO_H - size, VIDEO_W, VIDEO_H), fill=(8, 10, 14))


@lru_cache(maxsize=16)
def load_font(size=46):
    font_candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ]
    for font_path in font_candidates:
        if font_path.exists():
            return ImageFont.truetype(str(font_path), size)
    return ImageFont.load_default()


def wrap_subtitle(text, font, max_width):
    chars = list(str(text).strip())
    lines = []
    current = ""
    probe = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(probe)
    for char in chars:
        candidate = current + char
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if current and bbox[2] - bbox[0] > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines[:2]


def add_subtitle(frame, text):
    text = str(text or "").strip()
    if not text:
        return frame

    overlay = _SUBTITLE_OVERLAY_CACHE.get(text)
    if overlay is not None:
        return Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")

    font = load_font(42)
    lines = wrap_subtitle(text, font, VIDEO_W - 460)
    if not lines:
        return frame

    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    line_height = 58
    box_w = min(VIDEO_W - 340, 1240)
    box_h = 38 + line_height * len(lines)
    box_x = (VIDEO_W - box_w) // 2
    box_y = VIDEO_H - box_h - 96
    draw.rounded_rectangle(
        (box_x, box_y, box_x + box_w, box_y + box_h),
        radius=14,
        fill=(0, 0, 0, 122),
    )
    draw.line((box_x + 34, box_y, box_x + box_w - 34, box_y), fill=(255, 255, 255, 105), width=2)

    y = box_y + 20
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = (VIDEO_W - text_w) // 2
        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0, 175))
        draw.text((x, y), line, font=font, fill=(252, 252, 248, 244))
        y += line_height

    _SUBTITLE_OVERLAY_CACHE[text] = overlay
    return Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")


def clean_title(script):
    title = str(script.get("title") or script.get("xiaohongshu_title") or "").strip()
    if any(marker in title for marker in ("锛", "鑻", "鎱", "鏃", "琛")):
        repaired = title.encode("gbk", errors="ignore").decode("utf-8", errors="ignore").strip()
        if repaired:
            title = repaired
    title = title.replace("｜", " ").replace("|", " ").replace("\n", " ")
    title = title.replace("?", "").replace("？", "").replace("�", "").strip(" ，,。")
    if title.endswith("慢旅"):
        title += "行"
    if not title or any(marker in title for marker in ("�", "锛", "鑻", "鎱", "鏃", "琛")):
        return "旅行慢视频"
    return title[:28]


def closing_line(title):
    place = (
        title.replace("慢旅行", "")
        .replace("旅行", "")
        .replace("短片", "")
        .strip()
    )
    if not place or place == title:
        return "把这一段风景，慢慢留在路上。"
    return f"把这一份温柔，留在{place}。"


def draw_centered_text(draw, y, text, font, fill, max_width=1500):
    text = str(text or "").strip()
    if not text:
        return y

    lines = wrap_subtitle(text, font, max_width)
    line_height = int(font.size * 1.35) if hasattr(font, "size") else 64
    shadow_alpha = 150
    if isinstance(fill, tuple) and len(fill) == 4:
        shadow_alpha = min(shadow_alpha, fill[3])
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
        draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0, shadow_alpha))
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def add_cinematic_overlay(frame, strength=0.30):
    global _CINEMATIC_MASK_CACHE
    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, int(255 * strength)))
    frame = Image.alpha_composite(frame.convert("RGBA"), overlay)
    if _CINEMATIC_MASK_CACHE is None:
        vignette = Image.new("L", (VIDEO_W, VIDEO_H), 0)
        draw = ImageDraw.Draw(vignette)
        draw.ellipse((-260, -260, VIDEO_W + 260, VIDEO_H + 260), fill=210)
        vignette = Image.eval(vignette.filter(ImageFilter.GaussianBlur(110)), lambda v: 210 - v)
        shadow = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 92))
        _CINEMATIC_MASK_CACHE = (vignette, shadow)
    vignette, shadow = _CINEMATIC_MASK_CACHE
    frame = Image.composite(shadow, frame, vignette)
    return frame.convert("RGB")


def prepare_source_image(image):
    image = ImageOps.exif_transpose(image).convert("RGB")
    if not quality_env_enabled("VIDEO_AUTO_ENHANCE", True):
        return image

    image = ImageEnhance.Color(image).enhance(float(os.getenv("VIDEO_COLOR", "1.08")))
    image = ImageEnhance.Contrast(image).enhance(float(os.getenv("VIDEO_CONTRAST", "1.06")))
    image = ImageEnhance.Brightness(image).enhance(float(os.getenv("VIDEO_BRIGHTNESS", "1.01")))
    image = image.filter(ImageFilter.UnsharpMask(radius=1.0, percent=72, threshold=3))
    return ImageEnhance.Sharpness(image).enhance(float(os.getenv("VIDEO_SHARPNESS", "1.04")))


def add_film_grade(frame, progress=None, duration=None):
    frame = ImageEnhance.Color(frame).enhance(float(os.getenv("VIDEO_FRAME_COLOR", "1.015")))
    frame = ImageEnhance.Contrast(frame).enhance(float(os.getenv("VIDEO_FRAME_CONTRAST", "1.018")))

    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (255, 246, 232, 10))
    frame = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")

    if progress is not None:
        fade_in = 1.0 - ease_out(min(progress / 0.10, 1.0))
        fade_out = 0.0
        if duration and duration > 0:
            remaining = 1.0 - progress
            fade_out = 1.0 - ease_out(min(remaining / 0.12, 1.0))
        fade = max(0.0, min(0.32, max(fade_in, fade_out) * 0.32))
        if fade > 0:
            black = Image.new("RGB", (VIDEO_W, VIDEO_H), (4, 5, 7))
            frame = Image.blend(frame, black, fade)

    return frame


def make_title_clip(images, title, duration=TITLE_DURATION):
    first = images[0]

    def make_frame(t):
        progress = 0.0 if duration <= 0 else clamp(t / duration)
        frame = moving_cover(first, VIDEO_W, VIDEO_H, progress, "in", 0.18)
        frame = add_cinematic_overlay(frame, 0.42 - 0.12 * ease(progress))
        overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        p = ease(progress)
        accent_w = int(520 * p)
        draw.rectangle(((VIDEO_W - accent_w) // 2, 390, (VIDEO_W + accent_w) // 2, 398), fill=(255, 255, 255, 210))
        y = 428 - int(32 * (1 - p))
        draw_centered_text(draw, y, title, load_font(82), (255, 255, 255, 248), max_width=1320)
        draw_centered_text(draw, 560, "跟着画面慢慢走进风景", load_font(38), (232, 238, 245, 230), max_width=1100)
        frame = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")
        return np.asarray(frame, dtype=np.uint8)

    clip = VideoClip(make_frame, duration=duration)
    if hasattr(clip, "with_fps"):
        return clip.with_fps(FPS)
    return clip.set_fps(FPS)


def make_outro_clip(images, title, duration=OUTRO_DURATION):
    last = images[-1]
    line = closing_line(title)

    def make_frame(t):
        progress = 0.0 if duration <= 0 else clamp(t / duration)
        frame = moving_cover(last, VIDEO_W, VIDEO_H, progress, "out", 0.10)
        fade = ease(max(0, progress - 0.45) / 0.55)
        frame = add_cinematic_overlay(frame, 0.18 + 0.45 * fade)
        overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        alpha = int(255 * ease(min(progress / 0.55, 1.0)) * (1.0 - 0.35 * fade))
        draw_centered_text(draw, 470, line, load_font(56), (255, 255, 255, alpha), max_width=1320)
        frame = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")
        if fade > 0:
            black = Image.new("RGB", (VIDEO_W, VIDEO_H), (6, 8, 12))
            frame = Image.blend(frame, black, fade * 0.78)
        return np.asarray(frame, dtype=np.uint8)

    clip = VideoClip(make_frame, duration=duration)
    if hasattr(clip, "with_fps"):
        return clip.with_fps(FPS)
    return clip.set_fps(FPS)


def paste_card(canvas, image, xy, border=10, shadow=18):
    x, y = xy
    w, h = image.size
    shadow_layer = Image.new("RGBA", (w + shadow * 2, h + shadow * 2), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    shadow_draw.rectangle((shadow, shadow, shadow + w, shadow + h), fill=(0, 0, 0, 130))
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(shadow // 2))
    canvas.paste(shadow_layer, (x - shadow, y - shadow), shadow_layer)

    card = Image.new("RGB", (w + border * 2, h + border * 2), (245, 247, 250))
    card.paste(image, (border, border))
    canvas.paste(card, (x - border, y - border))


def polygon_mask(points):
    mask = Image.new("L", (VIDEO_W, VIDEO_H), 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    return mask


def add_wipe(frame, progress, index):
    if VIDEO_TRANSITION_STYLE in {"none", "cut"}:
        return frame
    if VIDEO_TRANSITION_STYLE in {"pulse", "flash"}:
        if progress >= 0.08:
            return frame
        amount = 1.0 - progress / 0.08
        overlay = Image.new("RGB", (VIDEO_W, VIDEO_H), (245, 241, 231))
        return Image.blend(frame, overlay, 0.12 * amount)

    # A restrained opening fade keeps cuts intentional without swallowing the image.
    if progress >= 0.12:
        return frame
    amount = 1.0 - progress / 0.12
    width = int(VIDEO_W * amount * 0.58)
    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    if index % 2:
        draw.polygon([(VIDEO_W, 0), (VIDEO_W - width, 0), (VIDEO_W, VIDEO_H)], fill=(0, 0, 0, 72))
    else:
        draw.polygon([(0, 0), (width, 0), (0, VIDEO_H)], fill=(0, 0, 0, 72))
    return Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")


def layout_establishing(images, index, progress):
    directions = ["in", "right", "left", "up", "out", "down"]
    frame = moving_cover(images[index], VIDEO_W, VIDEO_H, progress, directions[index % len(directions)], 0.15)
    add_letterbox(frame)
    return frame


def layout_cinematic_full(images, index, progress):
    directions = ["in", "right", "left", "up", "out", "down"]
    frame = moving_cover(images[index], VIDEO_W, VIDEO_H, progress, directions[index % len(directions)], 0.11)
    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 0, VIDEO_W, 54), fill=(5, 7, 10, 78))
    draw.rectangle((0, VIDEO_H - 54, VIDEO_W, VIDEO_H), fill=(5, 7, 10, 78))
    return Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")


def layout_split_route(images, index, progress):
    current = images[index]
    neighbor = images[(index + 1) % len(images)] if len(images) > 1 else current
    frame = background_from(current)
    p = ease(progress)

    left = moving_cover(current, 1180, 820, progress, "right", 0.10)
    right = moving_cover(neighbor, 520, 820, 1 - progress, "left", 0.08)
    paste_card(frame, left, (90 - int(70 * (1 - p)), 130), border=8)
    paste_card(frame, right, (1305 + int(80 * (1 - p)), 130), border=8)

    draw = ImageDraw.Draw(frame)
    draw.rectangle((1258, 120, 1270, 960), fill=(245, 247, 250))
    return frame


def layout_diagonal_cut(images, index, progress):
    current = moving_cover(images[index], VIDEO_W, VIDEO_H, progress, "up", 0.10)
    neighbor = images[index - 1] if len(images) > 1 else images[index]
    second = moving_cover(neighbor, VIDEO_W, VIDEO_H, 1 - progress, "down", 0.10)

    shift = int(120 * math.sin(progress * math.pi))
    points = [(0, 0), (VIDEO_W, 0), (VIDEO_W, 455 + shift), (0, 760 + shift)]
    mask = polygon_mask(points)
    frame = second.copy()
    frame.paste(current, (0, 0), mask)

    line = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(line)
    draw.line((0, 760 + shift, VIDEO_W, 455 + shift), fill=(255, 255, 255, 180), width=8)
    return Image.alpha_composite(frame.convert("RGBA"), line).convert("RGB")


def layout_detail_stack(images, index, progress):
    current = images[index]
    neighbor = images[(index + 1) % len(images)] if len(images) > 1 else current
    frame = background_from(neighbor)
    p = ease(progress)

    large = moving_cover(current, 1180, 720, progress, "in", 0.14)
    small = moving_cover(neighbor, 590, 390, 1 - progress, "left", 0.08)

    large = large.rotate(-2.5 + 2.5 * p, expand=True, resample=RESAMPLE_BICUBIC)
    small = small.rotate(3.5 - 2.0 * p, expand=True, resample=RESAMPLE_BICUBIC)
    paste_card(frame, large, (150, 170 + int(30 * (1 - p))), border=10)
    paste_card(frame, small, (1130, 570 - int(30 * (1 - p))), border=8)
    return frame


def layout_triptych(images, index, progress):
    current = images[index]
    frame = Image.new("RGB", (VIDEO_W, VIDEO_H), (13, 19, 30))
    gap = 20
    panel_w = (VIDEO_W - 160 - gap * 2) // 3
    panel_h = 820
    y = 130

    for column in range(3):
        local_progress = clamp(progress + (column - 1) * 0.08)
        panel = moving_cover(current, panel_w, panel_h, local_progress, ["left", "in", "right"][column], 0.18)
        x = 80 + column * (panel_w + gap)
        y_offset = int((1 - ease(local_progress)) * (70 if column != 1 else -50))
        paste_card(frame, panel, (x, y + y_offset), border=6, shadow=14)

    return frame


def layout_postcard(images, index, progress):
    current = images[index]
    previous = images[index - 1] if len(images) > 1 else current
    frame = background_from(previous)
    p = ease(progress)

    main = resize_contain(current, 1380, 780)
    scale = 1.0 + 0.035 * math.sin(progress * math.pi)
    main = main.resize((int(main.size[0] * scale), int(main.size[1] * scale)), RESAMPLE_HIGH)
    x = (VIDEO_W - main.size[0]) // 2 + int(42 * math.sin((progress - 0.5) * math.pi))
    y = (VIDEO_H - main.size[1]) // 2
    paste_card(frame, main, (x, y), border=12, shadow=24)

    draw = ImageDraw.Draw(frame)
    draw.rectangle((0, 0, VIDEO_W, 86), fill=(10, 16, 26))
    draw.rectangle((0, VIDEO_H - 86, VIDEO_W, VIDEO_H), fill=(10, 16, 26))
    return frame


def image_ratio(image):
    width, height = image.size
    return width / height if height else 1.0


def needs_adaptive_showcase(image):
    ratio = image_ratio(image)
    return ratio < 0.95 or ratio > 2.05


def layout_adaptive_showcase(images, index, progress):
    current = images[index]
    frame = background_from(current)
    p = ease(progress)
    ratio = image_ratio(current)

    if ratio < 0.95:
        max_w = int(VIDEO_W * 0.62)
        max_h = int(VIDEO_H * 0.86)
    else:
        max_w = int(VIDEO_W * 0.88)
        max_h = int(VIDEO_H * 0.68)

    subject = resize_contain(current, max_w, max_h)
    scale = 1.0 + 0.025 * math.sin(progress * math.pi)
    subject = subject.resize((int(subject.size[0] * scale), int(subject.size[1] * scale)), RESAMPLE_HIGH)
    x = (VIDEO_W - subject.size[0]) // 2
    y = (VIDEO_H - subject.size[1]) // 2 + int(22 * (1 - p))
    paste_card(frame, subject, (x, y), border=8, shadow=24)

    shade = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shade)
    draw.rectangle((0, 0, VIDEO_W, 72), fill=(8, 10, 14, 96))
    draw.rectangle((0, VIDEO_H - 72, VIDEO_W, VIDEO_H), fill=(8, 10, 14, 96))
    return Image.alpha_composite(frame.convert("RGBA"), shade).convert("RGB")


def make_layout_frame(images, index, progress):
    if len(images) == 1:
        frame = layout_establishing(images, index, progress)
    else:
        layout = LAYOUTS[index % len(LAYOUTS)]
        if layout == "split_route":
            frame = layout_split_route(images, index, progress)
        elif layout == "diagonal_cut":
            frame = layout_diagonal_cut(images, index, progress)
        elif layout == "detail_stack":
            frame = layout_detail_stack(images, index, progress)
        elif layout == "triptych":
            frame = layout_triptych(images, index, progress)
        elif layout == "postcard":
            frame = layout_postcard(images, index, progress)
        else:
            frame = layout_establishing(images, index, progress)
    return add_wipe(frame, progress, index)


def save_debug_first_frame(images):
    first_frame = make_layout_frame(images, 0, 0)
    first_frame.save(DEBUG_FIRST_FRAME, quality=95)
    print(f"Saved debug first frame: {DEBUG_FIRST_FRAME}")


def load_source_images(image_files):
    images = []
    for image_path in image_files:
        with Image.open(image_path) as source:
            images.append(prepare_source_image(source))
            print(f"Loaded image: {image_path.name} | {source.size[0]} x {source.size[1]}")
    return images


def video_export_kwargs(has_audio):
    ffmpeg_params = [
        "-crf",
        VIDEO_CRF,
        "-profile:v",
        "high",
        "-level",
        "4.2",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
    kwargs = {
        "fps": FPS,
        "codec": "libx264",
        "audio": has_audio,
        "preset": VIDEO_PRESET,
        "threads": max(1, int(os.getenv("VIDEO_THREADS", str(DEFAULT_VIDEO_THREADS)))),
        "ffmpeg_params": ffmpeg_params,
    }
    if has_audio:
        kwargs["audio_codec"] = "aac"
        kwargs["audio_bitrate"] = AUDIO_BITRATE
    if VIDEO_BITRATE:
        kwargs["bitrate"] = VIDEO_BITRATE
    return kwargs


def make_clip(images, index, duration, subtitles=None):
    subtitles = subtitles or []

    def make_frame(t):
        progress = 0.0 if duration <= 0 else clamp(t / duration)
        frame = make_layout_frame(images, index, progress)
        if index < len(subtitles):
            frame = add_subtitle(frame, subtitles[index])
        return np.asarray(frame, dtype=np.uint8)

    clip = VideoClip(make_frame, duration=duration)
    if hasattr(clip, "with_fps"):
        return clip.with_fps(FPS)
    return clip.set_fps(FPS)


MOJIBAKE_MARKERS = ("锛", "锟", "鑻", "鎱", "鏃", "琛", "鍌", "榛", "娓")


def repair_text(text):
    value = str(text or "").strip()
    if not value:
        return ""
    if any(marker in value for marker in MOJIBAKE_MARKERS):
        repaired = value.encode("gbk", errors="ignore").decode("utf-8", errors="ignore").strip()
        if repaired:
            value = repaired
    return value.replace("\\n", "\n").replace("?", "").replace("�", "").strip()


def clean_title(script):
    title = repair_text(script.get("xiaohongshu_title") or script.get("title") or "")
    title = title.replace("｜", "：").replace("|", "：").replace("—", "：").replace("-", "：")
    title = " ".join(title.split())
    if title.endswith("慢旅"):
        title += "行"
    if not title:
        title = "旅行慢视频"

    if "：" not in title:
        place = title.replace("慢旅行", "").replace("旅行", "").strip()
        if "苏州" in title or place == "苏州":
            title = "苏州慢旅行：在古城里找回自己的节奏"
        elif place:
            title = f"{place}慢旅行：把风景走成一段故事"
        else:
            title = "旅行慢视频：把风景走成一段故事"
    return title[:34]


def split_display_title(title):
    if "：" in title:
        main, hook = title.split("：", 1)
        return main.strip(), hook.strip()
    return title.strip(), "把风景走成一段故事"


def closing_line(title):
    main, _hook = split_display_title(title)
    place = main.replace("慢旅行", "").replace("旅行", "").replace("短片", "").strip()
    if place:
        return f"{place}的温柔，到这里慢慢收住。"
    return "这一段风景，到这里慢慢收住。"


def plan_layouts(subtitles, total):
    if VIDEO_LAYOUT_MODE == "cinematic":
        return ["cinematic_full" for _index in range(total)]

    planned = []
    for index in range(total):
        text = repair_text(subtitles[index]) if index < len(subtitles) else ""
        if index == 0:
            layout = "establishing"
        elif index == total - 1:
            layout = "postcard"
        elif any(word in text for word in ("背包", "背影", "情侣", "游客", "她", "他", "人")):
            layout = "detail_stack" if index % 2 else "split_route"
        elif any(word in text for word in ("街", "巷", "桥", "河", "石阶", "路", "穿过", "走过")):
            layout = "split_route" if index % 2 else "diagonal_cut"
        elif any(word in text for word in ("荷", "亭", "白墙", "松影", "水墨", "细节", "园林")):
            layout = "triptych" if index % 2 else "postcard"
        elif any(word in text for word in ("黄昏", "夕阳", "晨雾", "倒映", "醒来", "远景")):
            layout = "establishing"
        else:
            layout = LAYOUTS[index % len(LAYOUTS)]
        planned.append(layout)
    return planned


def render_layout_frame(images, index, progress, layout):
    if needs_adaptive_showcase(images[index]):
        return layout_adaptive_showcase(images, index, progress)
    if len(images) == 1:
        return layout_cinematic_full(images, index, progress)
    if layout == "cinematic_full":
        return layout_cinematic_full(images, index, progress)
    if layout == "split_route":
        return layout_split_route(images, index, progress)
    if layout == "diagonal_cut":
        return layout_diagonal_cut(images, index, progress)
    if layout == "detail_stack":
        return layout_detail_stack(images, index, progress)
    if layout == "triptych":
        return layout_triptych(images, index, progress)
    if layout == "postcard":
        return layout_postcard(images, index, progress)
    return layout_establishing(images, index, progress)


def make_layout_frame(images, index, progress, layout=None):
    layout = layout or (LAYOUTS[index % len(LAYOUTS)] if len(images) > 1 else "establishing")
    frame = render_layout_frame(images, index, progress, layout)
    return add_wipe(frame, progress, index)


def save_debug_first_frame(images):
    first_frame = make_layout_frame(images, 0, 0, "establishing")
    first_frame.save(DEBUG_FIRST_FRAME, quality=95)
    print(f"Saved debug first frame: {DEBUG_FIRST_FRAME}")


def save_quality_contact_sheet(images, subtitles, layouts):
    if not quality_env_enabled("VIDEO_SAVE_CONTACT_SHEET", True):
        return

    thumbs = []
    for index, _image in enumerate(images):
        layout = layouts[index] if index < len(layouts) else None
        frame = make_layout_frame(images, index, 0.5, layout)
        subtitle = repair_text(subtitles[index]) if index < len(subtitles) else ""
        frame = add_cultural_marker(frame, index, 0.5, subtitle)
        if index < len(subtitles):
            frame = add_subtitle(frame, subtitle)
        frame = add_film_grade(frame, 0.5, 1.0)
        thumb = frame.resize((480, 270), RESAMPLE_HIGH)
        thumbs.append((index + 1, layout or "auto", thumb))

    columns = min(4, max(1, len(thumbs)))
    rows = math.ceil(len(thumbs) / columns)
    label_h = 34
    sheet = Image.new("RGB", (columns * 480, rows * (270 + label_h)), (18, 20, 24))
    draw = ImageDraw.Draw(sheet)
    font = load_font(18)
    for item_index, (shot_index, layout, thumb) in enumerate(thumbs):
        col = item_index % columns
        row = item_index // columns
        x = col * 480
        y = row * (270 + label_h)
        sheet.paste(thumb, (x, y + label_h))
        draw.text((x + 12, y + 8), f"{shot_index:02d}  {layout}", font=font, fill=(238, 241, 246))

    sheet.save(QUALITY_CONTACT_SHEET, quality=92)
    print(f"Saved quality contact sheet: {QUALITY_CONTACT_SHEET}")


def save_quality_report(image_files, images, layouts, duration, shot_durations=None, voice_paths=None):
    shot_durations = shot_durations or []
    voice_paths = voice_paths or []
    report = {
        "output": {
            "path": str(OUTPUT_FILE),
            "relative_path": "output/final.mp4",
            "exists": OUTPUT_FILE.exists(),
            "size_bytes": OUTPUT_FILE.stat().st_size if OUTPUT_FILE.exists() else 0,
            "duration_seconds": round(float(duration or 0), 2),
            "width": VIDEO_W,
            "height": VIDEO_H,
            "aspect_ratio": "16:9" if VIDEO_ENFORCE_16_9 else f"{VIDEO_W}:{VIDEO_H}",
            "fps": FPS,
            "codec": "libx264",
            "crf": VIDEO_CRF,
            "preset": VIDEO_PRESET,
            "layout_mode": VIDEO_LAYOUT_MODE,
            "transition_style": VIDEO_TRANSITION_STYLE,
        },
        "quality": {
            "auto_enhance": quality_env_enabled("VIDEO_AUTO_ENHANCE", True),
            "color": float(os.getenv("VIDEO_COLOR", "1.08")),
            "contrast": float(os.getenv("VIDEO_CONTRAST", "1.06")),
            "brightness": float(os.getenv("VIDEO_BRIGHTNESS", "1.01")),
            "sharpness": float(os.getenv("VIDEO_SHARPNESS", "1.04")),
            "frame_color": float(os.getenv("VIDEO_FRAME_COLOR", "1.015")),
            "frame_contrast": float(os.getenv("VIDEO_FRAME_CONTRAST", "1.018")),
            "culture_labels": VIDEO_CULTURE_LABELS,
            "max_shot_duration": VIDEO_MAX_SHOT_DURATION,
            "min_shot_duration": VIDEO_MIN_SHOT_DURATION,
            "min_voice_shot_duration": VIDEO_MIN_VOICE_SHOT_DURATION,
            "voiceover_tail_padding": VOICEOVER_TAIL_PADDING,
            "voiceover_rate": VOICEOVER_RATE,
        },
        "shots": [
            {
                "index": index + 1,
                "filename": image_files[index].name if index < len(image_files) else "",
                "source_width": images[index].size[0],
                "source_height": images[index].size[1],
                "layout": layouts[index] if index < len(layouts) else "auto",
                "duration_seconds": round(float(shot_durations[index]), 2) if index < len(shot_durations) else None,
                "voice_file": str(voice_paths[index].name) if index < len(voice_paths) and voice_paths[index] else "",
            }
            for index in range(len(images))
        ],
    }
    QUALITY_REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved quality report: {QUALITY_REPORT_FILE}")


def make_title_clip(images, title, duration=TITLE_DURATION):
    first = images[0]
    main_title, hook = split_display_title(title)

    def make_frame(t):
        progress = 0.0 if duration <= 0 else clamp(t / duration)
        frame = moving_cover(first, VIDEO_W, VIDEO_H, progress, "in", 0.24)
        frame = add_cinematic_overlay(frame, 0.48 - 0.14 * ease(progress))
        overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        p = ease(progress)
        accent_w = int(640 * p)
        draw.rectangle(((VIDEO_W - accent_w) // 2, 360, (VIDEO_W + accent_w) // 2, 368), fill=(255, 255, 255, 220))
        draw_centered_text(draw, 405 - int(38 * (1 - p)), main_title, load_font(86), (255, 255, 255, 250), max_width=1320)
        draw_centered_text(draw, 548, hook, load_font(46), (236, 240, 246, 238), max_width=1320)
        frame = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")
        frame = add_film_grade(frame, progress, duration)
        return np.asarray(frame, dtype=np.uint8)

    clip = VideoClip(make_frame, duration=duration)
    if hasattr(clip, "with_fps"):
        return clip.with_fps(FPS)
    return clip.set_fps(FPS)


def make_outro_clip(images, title, duration=OUTRO_DURATION):
    last = images[-1]
    line = closing_line(title)

    def make_frame(t):
        progress = 0.0 if duration <= 0 else clamp(t / duration)
        frame = moving_cover(last, VIDEO_W, VIDEO_H, progress, "out", 0.18)
        fade = ease(max(0, progress - 0.40) / 0.60)
        frame = add_cinematic_overlay(frame, 0.16 + 0.54 * fade)
        overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        alpha = int(255 * ease(min(progress / 0.50, 1.0)) * (1.0 - 0.45 * fade))
        draw_centered_text(draw, 455, line, load_font(58), (255, 255, 255, alpha), max_width=1380)
        frame = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")
        if fade > 0:
            black = Image.new("RGB", (VIDEO_W, VIDEO_H), (4, 6, 10))
            frame = Image.blend(frame, black, min(0.96, fade * 0.92))
        frame = add_film_grade(frame, progress, duration)
        return np.asarray(frame, dtype=np.uint8)

    clip = VideoClip(make_frame, duration=duration)
    if hasattr(clip, "with_fps"):
        return clip.with_fps(FPS)
    return clip.set_fps(FPS)


def make_clip(images, index, duration, subtitles=None, layouts=None):
    subtitles = subtitles or []
    layouts = layouts or []
    subtitle = repair_text(subtitles[index]) if index < len(subtitles) else ""
    layout = layouts[index] if index < len(layouts) else None

    def make_frame(t):
        progress = 0.0 if duration <= 0 else clamp(t / duration)
        frame = make_layout_frame(images, index, progress, layout)
        frame = add_cultural_marker(frame, index, progress, subtitle)
        if subtitle:
            frame = add_subtitle(frame, subtitle)
        frame = add_film_grade(frame, progress, duration)
        return np.asarray(frame, dtype=np.uint8)

    clip = VideoClip(make_frame, duration=duration)
    if hasattr(clip, "with_fps"):
        return clip.with_fps(FPS)
    return clip.set_fps(FPS)


def synthesize_voiceover(voiceover):
    voiceover = str(voiceover or "").strip()
    if not voiceover:
        return None

    VOICE_FILE.unlink(missing_ok=True)
    VOICE_FALLBACK_FILE.unlink(missing_ok=True)
    edge_command = [
        "python",
        "-m",
        "edge_tts",
        "--voice",
        "zh-CN-XiaoxiaoNeural",
        f"--rate={VOICEOVER_RATE}",
        "--pitch=+0Hz",
        "--text",
        voiceover,
        "--write-media",
        str(VOICE_FILE),
    ]
    try:
        subprocess.run(edge_command, check=True, capture_output=True, text=True, timeout=240)
        if VOICE_FILE.exists() and VOICE_FILE.stat().st_size > 0:
            return VOICE_FILE
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"Edge TTS failed, falling back to Windows voice: {exc}")

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as text_file:
        text_file.write(voiceover)
        text_path = Path(text_file.name)

    ps_script = r"""
param([string]$TextPath, [string]$OutPath)
Add-Type -AssemblyName System.Speech
$text = Get-Content -LiteralPath $TextPath -Raw -Encoding UTF8
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
$speaker.Rate = -2
$speaker.Volume = 100
$speaker.SetOutputToWaveFile($OutPath)
$speaker.Speak($text)
$speaker.Dispose()
"""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".ps1", delete=False) as script_file:
        script_file.write(ps_script)
        script_path = Path(script_file.name)

    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                str(text_path),
                str(VOICE_FALLBACK_FILE),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"Voiceover synthesis failed: {exc}")
        return None
    finally:
        text_path.unlink(missing_ok=True)
        script_path.unlink(missing_ok=True)

    return VOICE_FALLBACK_FILE if VOICE_FALLBACK_FILE.exists() and VOICE_FALLBACK_FILE.stat().st_size > 0 else None


def synthesize_voiceover_line(text, index, run_dir=None):
    text = str(text or "").strip()
    if not text:
        return None

    run_dir = run_dir or OUTPUT_DIR
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / f"voice_{index:02d}.mp3"
    fallback = run_dir / f"voice_{index:02d}.wav"
    safe_unlink(target)
    safe_unlink(fallback)
    try:
        import edge_tts

        async def save_with_edge_tts():
            communicate = edge_tts.Communicate(
                text,
                "zh-CN-XiaoxiaoNeural",
                rate=VOICEOVER_RATE,
                pitch="+0Hz",
            )
            await communicate.save(str(target))

        asyncio.run(save_with_edge_tts())
        if target.exists() and target.stat().st_size > 0:
            return target
    except Exception as exc:
        print(f"Edge TTS in-process segment {index:02d} failed, falling back: {exc}")

    edge_command = [
        "python",
        "-m",
        "edge_tts",
        "--voice",
        "zh-CN-XiaoxiaoNeural",
        f"--rate={VOICEOVER_RATE}",
        "--pitch=+0Hz",
        "--text",
        text,
        "--write-media",
        str(target),
    ]
    try:
        subprocess.run(edge_command, check=True, capture_output=True, text=True, timeout=90)
        if target.exists() and target.stat().st_size > 0:
            return target
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"Edge TTS segment {index:02d} failed: {exc}")

    return None


def safe_unlink(path):
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        print(f"Skip locked file: {path}")


def cleanup_old_voice_runs(max_age_hours=24):
    cutoff = time.time() - max_age_hours * 3600
    for path in OUTPUT_DIR.glob("voice_run_*"):
        if not path.is_dir():
            continue
        try:
            if path.stat().st_mtime > cutoff:
                continue
            for child in path.glob("*"):
                safe_unlink(child)
            path.rmdir()
        except OSError as exc:
            print(f"Skip voice run cleanup for {path}: {exc}")


def synthesize_voiceover_segments(voiceover_lines):
    cleanup_old_voice_runs()
    run_dir = OUTPUT_DIR / f"voice_run_{int(time.time())}_{os.getpid()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    indexed_lines = list(enumerate(voiceover_lines, start=1))
    paths = [None] * len(indexed_lines)
    if not indexed_lines:
        return paths

    workers = min(VOICEOVER_WORKERS, len(indexed_lines))
    if workers <= 1:
        for index, line in indexed_lines:
            paths[index - 1] = synthesize_voiceover_line(line, index, run_dir)
        return paths

    print(f"Synthesizing voiceover with {workers} parallel workers")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_index = {
            executor.submit(synthesize_voiceover_line, line, index, run_dir): index
            for index, line in indexed_lines
        }
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            try:
                paths[index - 1] = future.result()
            except Exception as exc:
                print(f"Voiceover segment {index:02d} failed: {exc}")
    return paths


def attach_audio(video, audio_path=None, voice_segments=None, shot_starts=None, shot_durations=None):
    audio_clips = []
    tracks = []

    if voice_segments:
        shot_starts = shot_starts or []
        shot_durations = shot_durations or []
        for index, audio_path in enumerate(voice_segments):
            if not audio_path:
                continue
            voice = AudioFileClip(str(audio_path))
            audio_clips.append(voice)
            limit = shot_durations[index] if index < len(shot_durations) else voice.duration
            if hasattr(voice, "subclipped"):
                voice = voice.subclipped(0, min(voice.duration, limit))
            elif hasattr(voice, "subclip"):
                voice = voice.subclip(0, min(voice.duration, limit))
            start = shot_starts[index] if index < len(shot_starts) else 0
            voice = voice.with_start(start) if hasattr(voice, "with_start") else voice.set_start(start)
            voice = voice.with_volume_scaled(VOICE_VOLUME) if hasattr(voice, "with_volume_scaled") else voice.volumex(VOICE_VOLUME)
            tracks.append(voice)
    elif audio_path:
        voice = AudioFileClip(str(audio_path))
        audio_clips.append(voice)
        if hasattr(voice, "subclipped"):
            voice = voice.subclipped(0, min(voice.duration, video.duration))
        elif hasattr(voice, "subclip"):
            voice = voice.subclip(0, min(voice.duration, video.duration))
        voice = voice.with_volume_scaled(VOICE_VOLUME) if hasattr(voice, "with_volume_scaled") else voice.volumex(VOICE_VOLUME)
        tracks.append(voice)

    if BGM_FILE.exists() and BGM_FILE.stat().st_size > 0:
        bgm = AudioFileClip(str(BGM_FILE))
        audio_clips.append(bgm)
        pieces = []
        total = 0.0
        while total < video.duration:
            pieces.append(bgm)
            total += bgm.duration
        bgm_track = concatenate_audioclips(pieces)
        if hasattr(bgm_track, "subclipped"):
            bgm_track = bgm_track.subclipped(0, video.duration)
        elif hasattr(bgm_track, "subclip"):
            bgm_track = bgm_track.subclip(0, video.duration)
        bgm_track = bgm_track.with_volume_scaled(BGM_VOLUME) if hasattr(bgm_track, "with_volume_scaled") else bgm_track.volumex(BGM_VOLUME)
        if hasattr(bgm_track, "audio_fadein"):
            bgm_track = bgm_track.audio_fadein(1.2).audio_fadeout(2.0)
        tracks.append(bgm_track)

    if not tracks:
        return video, audio_clips

    mixed = tracks[0] if len(tracks) == 1 else CompositeAudioClip(tracks)
    if hasattr(mixed, "with_duration"):
        mixed = mixed.with_duration(video.duration)
    elif hasattr(mixed, "set_duration"):
        mixed = mixed.set_duration(video.duration)
    audio_clips.append(mixed)
    if hasattr(video, "with_audio"):
        return video.with_audio(mixed), audio_clips
    return video.set_audio(mixed), audio_clips


def main():
    ensure_dirs()
    safe_unlink(RENDERING_OUTPUT_FILE)
    script = load_script_data()
    duration = load_duration_per_image(script)
    image_files = get_image_files()

    if not image_files:
        raise FileNotFoundError("images 文件夹里没有 jpg/jpeg/png/webp/bmp/tif/tiff 图片，请先上传或生成图片。")

    subtitles = load_subtitles(script, len(image_files))
    title = clean_title(script)
    voiceover_lines = load_voiceover_lines(script, len(image_files), subtitles)
    layouts = plan_layouts(subtitles, len(image_files))

    print(f"Found {len(image_files)} images:")
    for image_path in image_files:
        print(f"- {image_path.name}")

    print(f"Fallback duration: {duration:.2f}s; native shots follow voiceover duration when audio is available")
    print(f"Output: {VIDEO_W} x {VIDEO_H}, FPS={FPS}")
    print(f"Title: {title}")
    print(f"Subtitles: {len(subtitles)} lines")
    print(f"Voiceover: {len(voiceover_lines)} lines")

    engine = normalize_engine_name(os.getenv("VIDEO_RENDER_ENGINE") or script.get("video_engine"))
    if should_use_external_engine(engine):
        print(f"Rendering with external engine: {engine}")
        if engine == "kburns-slideshow":
            print("External engine uses its own fixed per-image duration and does not use voice-driven shot timing.")
            render_with_kburns_slideshow(image_files, subtitles, duration, RENDERING_OUTPUT_FILE, VIDEO_W, VIDEO_H, FPS)
        elif engine == "3d-ken-burns":
            print("External engine uses its own timing and does not use voice-driven shot timing.")
            render_with_3d_ken_burns(image_files, RENDERING_OUTPUT_FILE)
        else:
            raise ValueError(f"Unsupported external video engine: {engine}")
        promote_rendered_output(RENDERING_OUTPUT_FILE)
        print(f"Video generated: {OUTPUT_FILE}")
        return

    source_images = load_source_images(image_files)
    if VIDEO_SAVE_DEBUG_FIRST_FRAME:
        save_debug_first_frame(source_images)
    save_quality_contact_sheet(source_images, subtitles, layouts)

    clips = []
    final_video = None
    audio_clips = []
    voice_path = None
    voice_paths = []
    shot_starts = []
    shot_durations = []
    try:
        voice_paths = synthesize_voiceover_segments(voiceover_lines)
        usable_voice_paths = [path for path in voice_paths if path]
        if usable_voice_paths:
            print(f"Voiceover segments generated: {len(usable_voice_paths)}/{len(voiceover_lines)}")
        else:
            print("No voiceover audio generated.")
        shot_durations = shot_durations_for_timeline(voice_paths, subtitles, duration)

        print("Rendering opening title")
        clips.append(make_title_clip(source_images, title))
        timeline_cursor = TITLE_DURATION

        for index, image_path in enumerate(image_files):
            layout = layouts[index] if index < len(layouts) else "establishing"
            shot_duration = shot_durations[index] if index < len(shot_durations) else estimate_subtitle_duration("", duration)
            shot_starts.append(timeline_cursor)
            print(f"Rendering segment {index + 1:02d}: {image_path.name} | layout={layout} | {shot_duration:.2f}s")
            clips.append(make_clip(source_images, index, shot_duration, subtitles, layouts))
            timeline_cursor += shot_duration

        print("Rendering closing scene")
        clips.append(make_outro_clip(source_images, title))

        final_video = concatenate_videoclips(clips, method="compose")
        if usable_voice_paths:
            final_video, audio_clips = attach_audio(
                final_video,
                voice_segments=voice_paths,
                shot_starts=shot_starts,
                shot_durations=shot_durations,
            )
        else:
            final_video, audio_clips = attach_audio(final_video, None)

        final_video.write_videofile(str(RENDERING_OUTPUT_FILE), **video_export_kwargs(final_video.audio is not None))
        promote_rendered_output(RENDERING_OUTPUT_FILE)
        if VIDEO_SAVE_QUALITY_REPORT:
            save_quality_report(image_files, source_images, layouts, final_video.duration, shot_durations, voice_paths)

        print(f"Video generated: {OUTPUT_FILE}")
    finally:
        for audio_clip in audio_clips:
            audio_clip.close()
        if final_video is not None:
            final_video.close()
        for clip in clips:
            clip.close()


if __name__ == "__main__":
    main()
