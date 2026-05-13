from pathlib import Path


# 镜头排序规则：数字越小，越靠前。
# 第一版采用“文件名关键词识别”，稳定、便宜、容易调试。
SHOT_ORDER = {
    # 开场：先交代城市/地点/环境。
    "开场": 1,
    "远景": 1,
    "全景": 1,
    "航拍": 1,
    "清晨": 1,
    "城市": 1,

    # 进入城市肌理。
    "街景": 2,
    "老街": 2,
    "老巷": 2,
    "巷子": 2,
    "街道": 2,
    "石板路": 2,

    # 地标与建筑。
    "建筑": 3,
    "古厝": 3,
    "寺庙": 3,
    "开元寺": 3,
    "双塔": 3,
    "红砖": 3,
    "屋檐": 3,

    # 人物让画面有代入感。
    "人物": 4,
    "游客": 4,
    "背影": 4,
    "行走": 4,
    "簪花": 4,

    # 细节增强质感。
    "特写": 5,
    "细节": 5,
    "门窗": 5,
    "灯笼": 5,
    "树影": 5,

    # 文化、美食、生活气。
    "美食": 6,
    "小吃": 6,
    "非遗": 6,
    "文化": 6,
    "市井": 6,
    "店铺": 6,

    # 后半段进入情绪收束。
    "傍晚": 7,
    "黄昏": 7,
    "夕阳": 7,
    "夜景": 7,
    "灯光": 7,

    # 结尾镜头最后出现。
    "结尾": 8,
    "收尾": 8,
    "空镜": 8,
    "结束": 8,
}


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def get_shot_score(image_path):
    """根据图片文件名判断镜头类型分数。"""
    path = Path(image_path)
    name = path.stem.lower()

    for keyword, score in SHOT_ORDER.items():
        if keyword.lower() in name:
            return score

    # 没有标签的图片放到中后段，避免打乱开场。
    return 50


def sort_images_by_shot_type(image_paths):
    """按照镜头语言顺序排序图片。"""
    return sorted(
        image_paths,
        key=lambda p: (get_shot_score(p), Path(p).name.lower())
    )


def load_sorted_images(image_dir):
    """读取图片并按镜头类型排序。"""
    image_dir = Path(image_dir)
    image_files = [
        p for p in image_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    ]
    return sort_images_by_shot_type(image_files)
