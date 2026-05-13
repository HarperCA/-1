from pathlib import Path


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


VIDEO_TEMPLATES = {
    "travel": {
        "name": "Travel cinematic video",
        "default_duration": 6,
        "opening_hold": 0.4,
        "ending_hold": 0.8,
        "shot_rules": [
            {"label": "opening", "score": 1, "motion": "zoom_in", "keywords": ["opening", "wide", "city", "morning", "start", "远景", "全景", "清晨", "开场"]},
            {"label": "street", "score": 2, "motion": "pan_right", "keywords": ["street", "lane", "road", "alley", "街景", "老街", "老巷", "巷子", "街道"]},
            {"label": "building", "score": 3, "motion": "pan_up", "keywords": ["building", "temple", "tower", "landmark", "建筑", "古厝", "寺庙", "双塔", "红砖"]},
            {"label": "people", "score": 4, "motion": "zoom_in", "keywords": ["people", "visitor", "walk", "back", "人物", "游客", "背影", "行走", "簪花"]},
            {"label": "detail", "score": 5, "motion": "zoom_out", "keywords": ["detail", "closeup", "texture", "特写", "细节", "门窗", "灯笼", "树影"]},
            {"label": "culture_food", "score": 6, "motion": "pan_left", "keywords": ["food", "culture", "shop", "美食", "小吃", "非遗", "文化", "店铺"]},
            {"label": "evening", "score": 7, "motion": "pan_down", "keywords": ["evening", "night", "sunset", "light", "傍晚", "黄昏", "夕阳", "夜景", "灯光"]},
            {"label": "ending", "score": 8, "motion": "zoom_out", "keywords": ["ending", "final", "empty", "结尾", "收尾", "空镜", "最后"]},
        ],
    },
    "general": {
        "name": "General slideshow video",
        "default_duration": 5,
        "opening_hold": 0.2,
        "ending_hold": 0.4,
        "shot_rules": [],
    },
}


def get_template(template_name):
    if not template_name:
        template_name = "travel"
    return VIDEO_TEMPLATES.get(template_name, VIDEO_TEMPLATES["travel"])


def detect_shot(image_path, template):
    name = Path(image_path).stem.lower()
    for rule in template.get("shot_rules", []):
        for keyword in rule.get("keywords", []):
            if keyword.lower() in name:
                return {
                    "label": rule.get("label", "unknown"),
                    "score": rule.get("score", 50),
                    "motion": rule.get("motion", "zoom_in"),
                    "keyword": keyword,
                }
    return {"label": "unknown", "score": 50, "motion": "zoom_in", "keyword": ""}


def load_images_for_template(image_dir, template_name="travel"):
    image_dir = Path(image_dir)
    template = get_template(template_name)
    image_files = [
        p for p in image_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    ]
    return sorted(
        image_files,
        key=lambda p: (detect_shot(p, template)["score"], p.name.lower())
    )


def choose_motion(image_path, template_name="travel", fallback="zoom_in"):
    template = get_template(template_name)
    motion = detect_shot(image_path, template).get("motion")
    return motion or fallback


def describe_image_plan(image_path, template_name="travel"):
    template = get_template(template_name)
    info = detect_shot(image_path, template)
    keyword = info.get("keyword") or "none"
    return f"{info['label']} | keyword:{keyword} | motion:{info['motion']}"
