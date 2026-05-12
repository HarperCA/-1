from pathlib import Path
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE_DIR = Path(__file__).resolve().parents[1]
IMAGE_DIR = BASE_DIR / "images"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

W, H = 1080, 1920

SCENES = [
    ("古城清晨", "有些地方，不适合匆匆路过。"),
    ("老街慢行", "它更适合慢慢走，慢慢看。"),
    ("屋檐光影", "风从树梢穿过，光落在路面上。"),
    ("石板小巷", "老街、屋檐、石板路，都像是在轻声说话。"),
    ("街头烟火", "真正打动人的，往往不是宏大的风景。"),
    ("人间日常", "而是这些日常里的烟火气。"),
    ("旅人背影", "旅行有时候不是为了去很远。"),
    ("傍晚远景", "而是在某个瞬间，重新感受到生活。"),
]


def get_font(size):
    font_paths = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
    ]
    for path in font_paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def centered_text(draw, text, y, font, fill=(255, 255, 255)):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x = (W - tw) // 2
    for dx, dy in [(-3, -3), (3, -3), (-3, 3), (3, 3)]:
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=fill)


def generate_image(index, title, subtitle):
    y = np.linspace(0, 1, H)[:, None]
    x = np.linspace(0, 1, W)[None, :]

    r = np.broadcast_to((80 + index * 10 + 80 * y).clip(0, 255), (H, W))
    g = np.broadcast_to((100 + 60 * (1 - y)).clip(0, 255), (H, W))
    b = np.broadcast_to((130 + 50 * x).clip(0, 255), (H, W))
    arr = np.dstack([r, g, b]).astype(np.uint8)

    img = Image.fromarray(arr, "RGB")
    draw = ImageDraw.Draw(img)

    draw.ellipse((760, 230, 930, 400), fill=(255, 220, 150))

    for layer in range(3):
        pts = []
        yy = 930 + layer * 155
        for px in range(0, W + 180, 180):
            py = yy - int(70 * math.sin((px / 150 + index + layer) * 0.8))
            pts.append((px, py))
        draw.polygon([(0, H)] + pts + [(W, H)], fill=(45 + layer * 28, 58 + layer * 24, 62 + layer * 20))

    draw.rectangle((90, 910, 355, 1340), fill=(135, 68, 50))
    draw.polygon([(55, 910), (220, 780), (390, 910)], fill=(80, 38, 30))
    draw.rectangle((750, 950, 1030, 1410), fill=(122, 62, 48))
    draw.polygon([(720, 950), (900, 810), (1060, 950)], fill=(76, 35, 28))

    road = [(280, H), (800, H), (620, 1080), (460, 1080)]
    draw.polygon(road, fill=(95, 85, 70))
    for k in range(10):
        yy = 1130 + k * 78
        width = 110 + k * 35
        draw.line((W // 2 - width, yy, W // 2 + width, yy), fill=(130, 118, 100), width=3)

    img = img.filter(ImageFilter.GaussianBlur(radius=0.25))
    draw = ImageDraw.Draw(img)
    centered_text(draw, title, 230, get_font(88))
    centered_text(draw, "AI旅游伪视频示例图", 350, get_font(36), fill=(245, 245, 245))
    centered_text(draw, subtitle, 1530, get_font(46))

    output = IMAGE_DIR / f"{index:02d}.jpg"
    img.save(output, quality=92)
    print(f"已生成：{output}")


def main():
    for index, (title, subtitle) in enumerate(SCENES, start=1):
        generate_image(index, title, subtitle)


if __name__ == "__main__":
    main()
