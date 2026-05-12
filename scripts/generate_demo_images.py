from pathlib import Path
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter


BASE_DIR = Path(__file__).resolve().parents[1]
IMAGE_DIR = BASE_DIR / "images"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

# 横屏 16:9 示例图
W, H = 1920, 1080

SCENES = [
    ("古城清晨", "有些城市，不适合匆匆路过。"),
    ("西街老巷", "泉州古城，更适合慢慢走，慢慢看。"),
    ("红砖古厝", "红砖古厝、老巷屋檐，藏着闽南的时间。"),
    ("开元寺双塔", "开元寺双塔静静伫立，见证一座城的千年烟火。"),
    ("街头烟火", "街边小店、热汤升腾，是最真实的人间日常。"),
    ("簪花古巷", "簪花从古巷走过，把传统留在温柔的光里。"),
    ("旅人背影", "旅行有时候不是为了去很远。"),
    ("傍晚收尾", "而是在一座城里，重新感受到生活。"),
]

FONT_CACHE = {}


def get_font(size):
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


def centered_text(draw, text, y, font, fill=(255, 255, 255)):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x = (W - tw) // 2

    for dx, dy in [(-3, 0), (3, 0), (0, -3), (0, 3)]:
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0))

    draw.text((x, y), text, font=font, fill=fill)


def generate_image(index, title, subtitle):
    y = np.linspace(0, 1, H)[:, None]
    x = np.linspace(0, 1, W)[None, :]

    r = np.broadcast_to((90 + index * 8 + 70 * y).clip(0, 255), (H, W))
    g = np.broadcast_to((105 + 45 * (1 - y)).clip(0, 255), (H, W))
    b = np.broadcast_to((125 + 45 * x).clip(0, 255), (H, W))
    arr = np.dstack([r, g, b]).astype(np.uint8)

    img = Image.fromarray(arr, "RGB")
    draw = ImageDraw.Draw(img)

    # 太阳
    draw.ellipse((1500, 120, 1660, 280), fill=(255, 220, 150))

    # 远山/屋顶层次
    for layer in range(3):
        pts = []
        yy = 580 + layer * 120
        for px in range(0, W + 240, 240):
            py = yy - int(55 * math.sin((px / 180 + index + layer) * 0.8))
            pts.append((px, py))

        draw.polygon(
            [(0, H)] + pts + [(W, H)],
            fill=(45 + layer * 28, 58 + layer * 24, 62 + layer * 20)
        )

    # 左右红砖建筑
    draw.rectangle((110, 520, 480, 900), fill=(135, 68, 50))
    draw.polygon([(70, 520), (300, 390), (530, 520)], fill=(80, 38, 30))

    draw.rectangle((1390, 540, 1780, 930), fill=(122, 62, 48))
    draw.polygon([(1340, 540), (1585, 395), (1830, 540)], fill=(76, 35, 28))

    # 石板路
    road = [(670, H), (1250, H), (1060, 650), (850, 650)]
    draw.polygon(road, fill=(95, 85, 70))

    for k in range(7):
        yy = 700 + k * 58
        width = 110 + k * 55
        draw.line((W // 2 - width, yy, W // 2 + width, yy), fill=(130, 118, 100), width=3)

    img = img.filter(ImageFilter.GaussianBlur(radius=0.2))
    draw = ImageDraw.Draw(img)

    centered_text(draw, title, 170, get_font(86))
    centered_text(draw, "AI旅游伪视频横屏示例图", 275, get_font(38), fill=(245, 245, 245))
    centered_text(draw, subtitle, 875, get_font(48))

    output = IMAGE_DIR / f"{index:02d}.jpg"
    img.save(output, quality=92)
    print(f"已生成：{output}")


def main():
    for index, (title, subtitle) in enumerate(SCENES, start=1):
        generate_image(index, title, subtitle)


if __name__ == "__main__":
    main()
