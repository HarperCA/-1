from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).parent
THIRD_PARTY_DIR = BASE_DIR / "third_party"
GENERATED_CACHE_DIR = BASE_DIR / "generated_cache"


def normalize_engine_name(value: str | None) -> str:
    engine = (value or "native").strip().lower().replace("_", "-")
    aliases = {
        "default": "native",
        "builtin": "native",
        "local": "native",
        "kbvs": "kburns-slideshow",
        "kenburns": "kburns-slideshow",
        "3d": "3d-ken-burns",
        "3d-kenburns": "3d-ken-burns",
    }
    return aliases.get(engine, engine)


def should_use_external_engine(value: str | None) -> bool:
    return normalize_engine_name(value) in {"kburns-slideshow", "3d-ken-burns"}


def _ffmpeg_binary(name: str) -> str:
    configured = os.getenv(name.upper().replace("-", "_"))
    if configured:
        return configured
    found = shutil.which(name) or shutil.which(f"{name}.exe")
    if found:
        return found
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    return f"{name}.exe" if os.name == "nt" else name


def _write_kburns_file_list(image_files, duration, output_width, output_height, fps):
    GENERATED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    config_path = GENERATED_CACHE_DIR / "kburns_slideshow.json"
    directions = [
        ("center", "center", "in"),
        ("left", "top", "out"),
        ("right", "bottom", "in"),
        ("center", "top", "out"),
    ]
    slides = []
    for index, image_path in enumerate(image_files):
        x_dir, y_dir, z_dir = directions[index % len(directions)]
        slides.append(
            {
                "file": str(Path(image_path).resolve()),
                "slide_duration": float(duration),
                "fade_duration": 0.45,
                "zoom_direction_x": x_dir,
                "zoom_direction_y": y_dir,
                "zoom_direction_z": z_dir,
                "zoom_rate": 0.13,
                "scale_mode": "crop_center",
                "transition": "fade",
            }
        )

    payload = {
        "config": {
            "ffmpeg": _ffmpeg_binary("ffmpeg"),
            "ffprobe": _ffmpeg_binary("ffprobe"),
            "aubio": _ffmpeg_binary("aubioonset"),
            "output_width": int(output_width),
            "output_height": int(output_height),
            "slide_duration": float(duration),
            "slide_duration_min": 1,
            "fade_duration": 0.45,
            "transition": "fade",
            "fps": int(fps),
            "zoom_rate": 0.13,
            "zoom_direction_x": "random",
            "zoom_direction_y": "random",
            "zoom_direction_z": "random",
            "scale_mode": "crop_center",
            "loopable": False,
            "overwrite": True,
            "generate_temp": True,
            "delete_temp": True,
            "sync_to_audio": False,
            "sync_titles_to_slides": False,
            "is_synced_to_audio": False,
        },
        "slides": slides,
        "audio": [],
    }
    config_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return config_path


def render_with_kburns_slideshow(image_files, subtitles, duration, output_file, output_width, output_height, fps):
    repo_dir = THIRD_PARTY_DIR / "kburns-slideshow"
    cli_file = repo_dir / "kbvs-cli.py"
    if not cli_file.exists():
        raise FileNotFoundError("third_party/kburns-slideshow is missing; initialize Git submodules first.")

    # Subtitles are intentionally omitted here. The upstream ffmpeg subtitles filter is fragile
    # with non-ASCII Windows paths; the native engine remains the subtitle/voiceover path.
    file_list = _write_kburns_file_list(image_files, duration, output_width, output_height, fps)
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(cli_file), str(output_file.resolve()), "-f", str(file_list.resolve()), "-y"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "kburns-slideshow failed").strip()
        raise RuntimeError(message[-3000:])
    if not output_file.exists() or output_file.stat().st_size <= 0:
        raise RuntimeError("kburns-slideshow finished without creating an output file; check ffmpeg compatibility.")


def render_with_3d_ken_burns(image_files, output_file):
    repo_dir = THIRD_PARTY_DIR / "3d-ken-burns"
    script_file = repo_dir / "autozoom.py"
    if not script_file.exists():
        raise FileNotFoundError("third_party/3d-ken-burns is missing; initialize Git submodules first.")
    if len(image_files) != 1:
        raise ValueError("3d-ken-burns currently supports one image through this wrapper; keep only one image in images/.")

    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            sys.executable,
            str(script_file),
            "--in",
            str(Path(image_files[0]).resolve()),
            "--out",
            str(output_file.resolve()),
        ],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "3d-ken-burns failed").strip()
        raise RuntimeError(message[-3000:])
    if not output_file.exists() or output_file.stat().st_size <= 0:
        raise RuntimeError("3d-ken-burns finished without creating an output file; check CUDA, PyTorch, CuPy, and model weights.")

