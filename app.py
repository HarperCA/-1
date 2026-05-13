from pathlib import Path
import json
import subprocess
import sys

from flask import Flask, render_template, request, send_file, redirect, url_for, flash, jsonify
from api_clients import (
    analyze_images_with_qwen_vl,
    describe_api_status,
    generate_prompt_markdown_with_api,
    generate_scene_topics_with_api,
    get_api_settings,
    write_city_script_with_deepseek,
    write_script_with_deepseek,
)

BASE_DIR = Path(__file__).parent
IMAGE_DIR = BASE_DIR / "images"
AUDIO_DIR = BASE_DIR / "audio"
OUTPUT_DIR = BASE_DIR / "output"
SCRIPT_FILE = BASE_DIR / "script.json"
OUTPUT_FILE = OUTPUT_DIR / "final.mp4"
PROMPTS_FILE = BASE_DIR / "IMAGE_PROMPTS.md"

app = Flask(__name__)
app.secret_key = "travel-video-generator"

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
DEFAULT_SCENES = [
    "古城清晨",
    "西街老巷",
    "红砖古厝",
    "开元寺双塔",
    "街头生活感",
    "簪花古巷",
    "旅人背影",
    "傍晚收尾",
]

SCENE_PRESETS = {
    "泉州": ["古城清晨", "西街老巷", "红砖古厝", "开元寺双塔", "街头生活感", "簪花古巷", "旅人背影", "傍晚收尾"],
    "厦门": ["鼓浪屿晨光", "海边栈道", "骑楼老街", "南普陀寺", "沙坡尾生活", "环岛路骑行", "海风旅人", "日落海湾"],
    "北京": ["中轴线晨光", "胡同深处", "故宫红墙", "景山远眺", "老北京烟火", "四合院门楼", "旅人漫步", "黄昏城楼"],
    "上海": ["外滩晨光", "武康路街景", "石库门弄堂", "陆家嘴天际线", "咖啡店日常", "梧桐树影", "城市旅人", "黄浦江夜色"],
    "杭州": ["西湖晨雾", "断桥远景", "灵隐寺山门", "龙井茶园", "湖边生活感", "苏堤漫步", "旅人背影", "夕照湖面"],
    "苏州": ["平江路清晨", "小桥流水", "苏式园林", "白墙黛瓦", "茶馆生活", "评弹巷口", "旅人慢行", "古城傍晚"],
    "成都": ["宽窄巷晨光", "锦里街景", "茶馆日常", "老街烟火", "熊猫元素远景", "川西院落", "旅人慢逛", "夜色灯火"],
    "重庆": ["山城晨雾", "洪崖洞层楼", "江边步道", "轻轨穿城", "老街梯坎", "火锅街头", "旅人背影", "两江夜景"],
    "西安": ["城墙晨光", "回民街烟火", "大雁塔远景", "唐风建筑", "老巷生活", "碑林街景", "旅人漫步", "古都夜色"],
    "南京": ["秦淮河晨色", "夫子庙街景", "明城墙树影", "梧桐大道", "老门东烟火", "民国建筑", "旅人背影", "金陵傍晚"],
    "广州": ["骑楼晨光", "珠江岸线", "西关老街", "早茶生活", "粤式建筑", "城市绿荫", "旅人慢行", "珠江夜色"],
    "深圳": ["海岸晨光", "城市天际线", "人才公园", "创意园街景", "湾区生活", "绿道漫步", "旅人背影", "日落海湾"],
    "大理": ["洱海晨光", "古城街巷", "苍山远景", "白族民居", "咖啡小店", "环海公路", "旅人背影", "洱海日落"],
    "丽江": ["古城清晨", "石板老街", "木府建筑", "纳西庭院", "溪水巷口", "手鼓小店", "旅人慢行", "古城夜色"],
    "长沙": ["湘江晨光", "橘子洲远景", "老街烟火", "文和友街景", "夜市生活", "城市霓虹", "旅人背影", "江边夜色"],
    "武汉": ["江汉关晨光", "黄鹤楼远景", "长江大桥", "汉口老街", "过早生活", "江滩漫步", "旅人背影", "两江夜色"],
}


def generate_default_scenes(destination):
    destination = (destination or "").strip()
    if not destination:
        return DEFAULT_SCENES

    for keyword, scenes in SCENE_PRESETS.items():
        if keyword in destination:
            return scenes

    short_name = destination
    for suffix in ["古城", "老城", "市", "旅游", "旅行", "景区"]:
        short_name = short_name.replace(suffix, "")
    short_name = short_name.strip() or destination

    return [
        f"{short_name}清晨",
        f"{short_name}街巷",
        "地标建筑",
        "地方建筑细节",
        "街头生活感",
        "本地文化元素",
        "旅人背影",
        "傍晚收尾",
    ]


def fit_scene_count(scenes, count, destination=""):
    scenes = [scene for scene in scenes if scene]
    if not scenes:
        scenes = generate_default_scenes(destination)
    extras = ["晨光开场", "远景航拍", "地标特写", "街头日常", "文化细节", "旅人漫步", "傍晚光影", "夜色收尾"]
    index = 0
    while len(scenes) < count:
        candidate = extras[index % len(extras)]
        if candidate in scenes:
            candidate = f"{candidate}{index + 1}"
        scenes.append(candidate)
        index += 1
    return scenes[:count]


def motion_strategy_for_destination(destination):
    destination = destination or ""
    if any(keyword in destination for keyword in ["桂林", "阳朔", "山水", "漓江", "大理", "洱海", "张家界", "黄山"]):
        return "山水型运镜：航拍或高机位远景建立空间，水面低机位缓慢推进，船只/竹筏侧向跟拍，倒影镜头稳定悬停，山体局部用仰拍或轻微上摇。"
    if any(keyword in destination for keyword in ["重庆", "山城", "香港"]):
        return "山城型运镜：利用高低落差俯拍，楼梯/坡道跟拍，建筑纵深推进，轻轨或道路横向平移，夜景结尾缓慢拉远。"
    if any(keyword in destination for keyword in ["苏州", "乌镇", "周庄", "水乡", "园林"]):
        return "园林水乡型运镜：小桥水面横向平移，窗棂和树枝作前景带入，对称构图稳定悬停，曲径和回廊慢速推进。"
    if any(keyword in destination for keyword in ["上海", "深圳", "广州", "都市", "外滩", "陆家嘴"]):
        return "现代都市型运镜：天际线远景拉远，道路纵深推进，玻璃幕墙反射构图，江面或街区稳定横移，夜景灯光做收束。"
    if any(keyword in destination for keyword in ["北京", "西安", "南京", "泉州", "丽江", "古城", "老街", "胡同"]):
        return "古城老街型运镜：巷道平视慢推，门窗框景，转角横向平移，建筑细节近景切入，生活烟火用中景跟拍。"
    return "综合文旅运镜：先用远景建立环境，中段穿插中景生活与近景细节，适当使用平移、推进、拉远和稳定悬停，结尾用暮色或远景收束。"

VOICEOVER_TONES = {
    "healing": {
        "label": "治愈文艺",
        "speed_factor": 1.00,
        "templates": {
            "开场": "有些地方，不适合匆匆走过。",
            "清晨": "{destination}更适合在柔和的光里慢慢靠近。",
            "老巷": "老街、石板路和屋檐，把时间留在了转角处。",
            "建筑": "古老建筑静静伫立，像是在守望这座城的从前。",
            "生活": "街边小店亮起温暖的光，日常也有了旅行的味道。",
            "旅人": "旅人穿过安静的巷子，也把脚步放慢了一点。",
            "傍晚": "傍晚的光落下来，整座城变得温柔而安静。",
            "收尾": "离开时才发现，{destination}留下的是慢慢生活的感觉。",
            "城市印象": "{destination}不适合匆匆路过，更适合慢慢走，慢慢看。",
        },
    },
    "documentary": {
        "label": "纪录片旁白",
        "speed_factor": 1.05,
        "templates": {
            "开场": "这里是{destination}，一座在时间里慢慢生长的城市。",
            "清晨": "清晨的光照进街巷，也唤醒了古城的轮廓。",
            "老巷": "老巷保留着生活的纹理，也记录着城市的记忆。",
            "建筑": "古老建筑安静伫立，延续着这片土地的文脉。",
            "生活": "街边的日常场景，构成了城市最真实的温度。",
            "旅人": "行走其间，人们更容易感受到时间的缓慢流动。",
            "傍晚": "当暮色落下，古城呈现出另一种沉静的面貌。",
            "收尾": "{destination}的魅力，不在喧闹，而在日复一日的从容。",
            "城市印象": "{destination}用安静的方式，保存着一座城的过去与现在。",
        },
    },
    "xiaohongshu": {
        "label": "小红书种草",
        "speed_factor": 0.90,
        "templates": {
            "开场": "来{destination}，一定要留点时间慢慢逛。",
            "清晨": "清晨的古城很安静，随手一拍都很有氛围。",
            "老巷": "老街和巷子真的适合慢慢走，越逛越有味道。",
            "建筑": "红砖、屋檐和古建筑，是这里最特别的记忆点。",
            "生活": "街边小店和日常光影，让旅程变得很舒服。",
            "旅人": "在这里不用赶路，放慢脚步反而更好看。",
            "傍晚": "傍晚的光一出来，整座古城都温柔了。",
            "收尾": "如果你也喜欢慢旅行，{destination}真的值得来。",
            "城市印象": "{destination}很适合慢慢逛，安静又有氛围。",
        },
    },
    "douyin": {
        "label": "抖音短视频",
        "speed_factor": 0.82,
        "templates": {
            "开场": "这座城，真的适合慢下来。",
            "清晨": "清晨走进{destination}，氛围感直接拉满。",
            "老巷": "老街一拐弯，就能遇见不一样的风景。",
            "建筑": "红砖古厝和老建筑，是这里最特别的底色。",
            "生活": "街边日常一亮起来，旅行感就有了。",
            "旅人": "别走太快，慢一点才看得见这座城。",
            "傍晚": "傍晚这一刻，真的很适合收尾。",
            "收尾": "来{destination}，把脚步放慢一次。",
            "城市印象": "{destination}，是一座越慢走越有味道的城。",
        },
    },
    "promo": {
        "label": "沉稳宣传片",
        "speed_factor": 1.00,
        "templates": {
            "开场": "走进{destination}，感受古城文脉与城市温度。",
            "清晨": "晨光之中，古城街巷展现出宁静而悠远的气质。",
            "老巷": "一砖一瓦之间，承载着地方文化的独特记忆。",
            "建筑": "历史建筑与城市生活相互映照，构成独特风貌。",
            "生活": "真实的街巷日常，让这座城市更具亲近感。",
            "旅人": "漫步其中，可以感受传统与当下的自然连接。",
            "傍晚": "暮色渐起，古城呈现出温和而诗意的景象。",
            "收尾": "{destination}，是一处值得停留、品味与再次抵达的地方。",
            "城市印象": "{destination}，以深厚底蕴呈现独特的文旅魅力。",
        },
    },
    "citywalk": {
        "label": "城市漫游",
        "speed_factor": 0.95,
        "templates": {
            "开场": "这一次，我们把脚步交给{destination}。",
            "清晨": "从清晨开始，沿着街巷慢慢往前走。",
            "老巷": "巷子不宽，却藏着很多值得停下来的细节。",
            "建筑": "屋檐、墙面和光影，组成了这段漫游的节奏。",
            "生活": "街边的日常声音，让旅程变得更真实。",
            "旅人": "走着走着，人也跟着这座城慢了下来。",
            "傍晚": "到了傍晚，光线把街巷染得更柔和。",
            "收尾": "这趟漫游结束了，但{destination}的余味还在。",
            "城市印象": "在{destination}漫游，最重要的是别走太快。",
        },
    },
}


def ensure_dirs():
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_image_files():
    ensure_dirs()
    return sorted([p for p in IMAGE_DIR.iterdir() if p.suffix.lower() in ALLOWED_IMAGE_EXTS])


def load_script():
    if SCRIPT_FILE.exists():
        return json.loads(SCRIPT_FILE.read_text(encoding="utf-8"))
    return {
        "title": "慢慢抵达泉州古城",
        "duration_per_image": 6,
        "aspect_ratio": "16:9",
        "voiceover_tone": "healing",
        "subtitles": [],
        "voiceover": ""
    }


def save_script(data):
    SCRIPT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def plan_video_timing(video_duration):
    try:
        video_duration = int(float(video_duration))
    except (TypeError, ValueError):
        video_duration = 60

    video_duration = max(10, min(600, video_duration))
    preferred_seconds = 5
    image_count = max(3, round(video_duration / preferred_seconds))
    image_count = min(80, image_count)
    duration_per_image = round(video_duration / image_count, 1)

    return {
        "video_duration": video_duration,
        "image_count": image_count,
        "duration_per_image": duration_per_image,
    }


def default_prompt_form():
    destination = "泉州古城"
    video_duration = 60
    timing_plan = plan_video_timing(video_duration)
    return {
        "destination": destination,
        "video_duration": video_duration,
        "image_count": timing_plan["image_count"],
        "planned_duration_per_image": timing_plan["duration_per_image"],
        "aspect_ratio": "16:9",
        "style_keywords": "真实摄影、电影感、纪录片风格、自然光、慢节奏文旅宣传片、高清、真实细节、自然色彩、干净画面、远景人物、背影、无清晰面部",
        "negative_keywords": "避免文字、避免水印、避免Logo、避免插画风、避免卡通感、避免AI感、避免乱码招牌、避免错误建筑结构、避免不自然建筑比例、避免近景人像、避免清晰面部、避免夸张肢体、避免过度滤镜",
        "scenes_text": "\n".join(generate_default_scenes(destination))
    }


def read_prompt_output():
    if PROMPTS_FILE.exists():
        return PROMPTS_FILE.read_text(encoding="utf-8")
    return ""


def image_path_from_name(filename):
    """根据文件名获取 images 目录下的安全图片路径，防止误删目录外文件。"""
    if not filename:
        return None

    filename = Path(filename).name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_IMAGE_EXTS:
        return None

    image_dir = IMAGE_DIR.resolve()
    image_path = (IMAGE_DIR / filename).resolve()
    if image_path.parent != image_dir:
        return None

    return image_path


def next_image_index():
    """获取下一张图片编号。删除过图片时不强制补位，保证不覆盖现有文件。"""
    max_index = 0
    for image_file in IMAGE_DIR.iterdir():
        if image_file.suffix.lower() not in ALLOWED_IMAGE_EXTS:
            continue
        stem = image_file.stem
        if stem.isdigit():
            max_index = max(max_index, int(stem))
    return max_index + 1


def guess_destination(title):
    title = (title or "").strip()
    if "泉州" in title:
        return "泉州古城"
    if "古城" in title:
        return title.replace("慢慢抵达", "").replace("慢慢走进", "").strip() or "这座古城"
    if title:
        cleaned = title.replace("慢慢抵达", "").replace("慢慢走进", "").replace("的一天", "").strip()
        return cleaned or title
    return "这座城"


def normalize_sentence_length(text, max_chars):
    """控制每句旁白长度，避免朗读时间明显超过单张图片播放时长。"""
    text = text.strip()
    if len(text) <= max_chars:
        return text

    cut_points = ["，", "。", "；", "、"]
    for point in cut_points:
        pos = text.rfind(point, 0, max_chars + 1)
        if pos >= max(8, int(max_chars * 0.55)):
            return text[:pos + 1]

    return text[:max_chars].rstrip("，、；。") + "。"


def scene_name_for_index(index, total_count):
    """根据图片顺序给一个镜头语义，不固定 8 张。"""
    if total_count <= 1:
        return "城市印象"

    ratio = index / max(total_count - 1, 1)
    if index == 0:
        return "开场"
    if index == total_count - 1:
        return "收尾"
    if ratio < 0.18:
        return "清晨"
    if ratio < 0.34:
        return "老巷"
    if ratio < 0.50:
        return "建筑"
    if ratio < 0.66:
        return "生活"
    if ratio < 0.82:
        return "旅人"
    return "傍晚"


def generate_voiceover_lines(title, image_count, duration_per_image, tone="healing"):
    """
    根据图片数量、播放速度和语气自动生成旁白。
    规则：一张图片一行旁白；图片停留越短，每句越短。
    """
    image_count = max(1, int(image_count))
    duration_per_image = max(2, int(duration_per_image))
    destination = guess_destination(title)
    tone_config = VOICEOVER_TONES.get(tone, VOICEOVER_TONES["healing"])

    speed_factor = tone_config.get("speed_factor", 1.0)
    max_chars = max(10, min(38, int(duration_per_image * 3.8 * speed_factor)))
    templates = tone_config["templates"]

    lines = []
    for index in range(image_count):
        scene_name = scene_name_for_index(index, image_count)
        text_template = templates.get(scene_name, templates.get("城市印象", "{destination}的这一刻，安静而有生活气息。"))
        text = text_template.format(destination=destination)
        lines.append(normalize_sentence_length(text, max_chars))

    return lines


def scene_detail(destination, scene_name):
    name = scene_name.strip()

    if "清晨" in name:
        return f"清晨的{destination}真实摄影远景，老街巷铺展开来，远处有地标轮廓，晨光柔和，空气通透，树影落在屋檐和石板路上，画面安静怀旧"
    if "西街" in name or "老巷" in name:
        return f"{destination}老街巷真实摄影画面，传统建筑、石板路、街边小店与远景人物自然入镜，温暖自然光，生活气息真实"
    if "红砖" in name or "古厝" in name:
        return f"{destination}闽南红砖古厝真实摄影，老屋屋檐、红砖墙、石板路和斑驳墙面，阳光从巷子上方斜斜落下，建筑细节真实，安静怀旧，浅景深"
    if "双塔" in name or "开元寺" in name or "寺" in name:
        return f"{destination}开元寺双塔真实摄影，古老石塔立在寺庙庭院中，树影斑驳，建筑比例真实，画面庄重安静，有历史感"
    if "生活" in name or "小吃" in name or "街头" in name:
        return f"{destination}街头生活场景真实摄影，街边小店、传统小吃摊、自然行走的远景人物，画面温暖克制，具有城市日常感"
    if "簪花" in name:
        return f"闽南簪花人物背影走在{destination}红砖古厝巷子里，传统簪花头饰，阳光照在红砖墙面和石板路上，画面温柔安静，有地方文化气息"
    if "旅人" in name or "背影" in name:
        return f"旅人背影慢慢走过{destination}老巷，墙面斑驳，石板路上有温暖阳光，巷子安静治愈，城市漫游感明显"
    if "傍晚" in name or "收尾" in name or "黄昏" in name:
        return f"傍晚的{destination}远景真实摄影，城市建筑安静铺展，天空微橙，远处光线柔和，诗意文旅宣传片结尾画面"

    return f"{destination}{name}真实摄影画面，保留地域文化特色与生活气息，画面自然、安静、适合文旅宣传片"


def get_orientation_text(aspect_ratio):
    ratio = aspect_ratio.replace(" ", "")
    if ratio == "9:16":
        return "竖屏"
    if ratio == "1:1":
        return "方图"
    return "横屏"


def build_single_prompt(destination, scene_name, style_keywords, negative_keywords, aspect_ratio):
    detail = scene_detail(destination, scene_name)
    orientation = get_orientation_text(aspect_ratio)
    safe_people_text = "人物只作为远景或背影出现，无清晰面部"
    camera_text = (
        f"{motion_strategy_for_destination(destination)}"
        "连续文旅短片分镜，统一真实摄影色彩和自然光质感，"
        "明确景别、拍摄角度、主体大小、前景中景背景层次，"
        "适合后期缓慢推进、轻微拉远或横向平移运镜，构图稳定，画面连贯"
    )
    return f"{detail}，{camera_text}，{style_keywords}，{safe_people_text}，{aspect_ratio}{orientation}，{negative_keywords}"


def build_prompt_document(destination, aspect_ratio, style_keywords, negative_keywords, scenes, timing_plan=None):
    timing_plan = timing_plan or {}
    lines = []
    lines.append(f"# {destination}旅游伪视频图片生成提示词｜{aspect_ratio}版")
    lines.append("")
    lines.append("用途：用于即梦、豆包、通义万相、可灵图片等工具生成旅游短视频配图。")
    if timing_plan:
        lines.append("")
        lines.append(
            f"视频规划：总时长约 {timing_plan.get('video_duration')} 秒，"
            f"建议生成 {timing_plan.get('image_count')} 张图，"
            f"每张图停留约 {timing_plan.get('duration_per_image')} 秒。"
        )
    lines.append("")
    lines.append("建议统一参数：")
    lines.append("")
    lines.append("```text")
    lines.append(f"目的地：{destination}")
    lines.append(f"画幅比例：{aspect_ratio}")
    lines.append(f"风格：{style_keywords}")
    lines.append(f"避免项：{negative_keywords}")
    lines.append(f"城市运镜策略：{motion_strategy_for_destination(destination)}")
    lines.append("统一镜头策略：作为同一支连续文旅短片，保持天气、色彩、光线、摄影质感统一；远景/中景/近景/特写交替；每张图明确景别、角度、主体大小、画面层次和适合的运镜方向。")
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")

    for idx, scene in enumerate(scenes, start=1):
        num = f"{idx:02d}"
        prompt = build_single_prompt(destination, scene, style_keywords, negative_keywords, aspect_ratio)
        lines.append(f"## {num}.jpg {scene}")
        lines.append("")
        lines.append("```text")
        lines.append(prompt)
        lines.append("```")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 使用方式")
    lines.append("")
    lines.append("1. 复制上面每一条提示词到图片生成工具。")
    lines.append(f"2. 统一选择 {aspect_ratio} 画幅。")
    lines.append("3. 生成图片后按顺序命名为 01.jpg、02.jpg、03.jpg ...")
    lines.append("4. 放入项目的 images/ 文件夹。")
    lines.append("5. 回到本项目网页继续生成旅游伪视频。")
    lines.append("")
    return "\n".join(lines)


def build_view_data(prompt_output=None, prompt_form=None):
    ensure_dirs()
    data = load_script()
    image_files = sorted([p.name for p in IMAGE_DIR.iterdir() if p.suffix.lower() in ALLOWED_IMAGE_EXTS])
    has_output = OUTPUT_FILE.exists()
    bgm_exists = (AUDIO_DIR / "bgm.mp3").exists()
    voice_exists = (AUDIO_DIR / "voice.mp3").exists()

    if prompt_output is None:
        prompt_output = read_prompt_output()
    if prompt_form is None:
        prompt_form = default_prompt_form()

    return {
        "data": data,
        "image_files": image_files,
        "has_output": has_output,
        "bgm_exists": bgm_exists,
        "voice_exists": voice_exists,
        "prompt_output": prompt_output,
        "prompt_form": prompt_form,
        "voiceover_tones": VOICEOVER_TONES,
        "current_voiceover_tone": data.get("voiceover_tone", "healing"),
        "api_status": describe_api_status(),
        "api_settings": get_api_settings(),
    }


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", **build_view_data())


@app.route("/save", methods=["POST"])
def save():
    ensure_dirs()
    title = request.form.get("title", "慢慢抵达一座城").strip()
    duration = int(request.form.get("duration_per_image", "6"))
    subtitles_text = request.form.get("subtitles", "").strip()
    voiceover = request.form.get("voiceover", "").strip()
    voiceover_tone = request.form.get("voiceover_tone", "healing")
    subtitles = [line.strip() for line in subtitles_text.splitlines() if line.strip()]

    data = {
        "title": title,
        "duration_per_image": duration,
        "aspect_ratio": "16:9",
        "voiceover_tone": voiceover_tone,
        "subtitles": subtitles,
        "voiceover": voiceover or "\n".join(subtitles)
    }
    save_script(data)
    flash("脚本和文案已保存。")
    return redirect(url_for("index"))


@app.route("/generate_voiceover", methods=["POST"])
def generate_voiceover():
    ensure_dirs()
    data = load_script()
    title = request.form.get("title", data.get("title", "慢慢抵达一座城")).strip()
    duration = int(request.form.get("duration_per_image", data.get("duration_per_image", 6)))
    voiceover_tone = request.form.get("voiceover_tone", data.get("voiceover_tone", "healing"))
    if voiceover_tone not in VOICEOVER_TONES:
        voiceover_tone = "healing"
    image_count = len(get_image_files()) or 8
    tone_label = VOICEOVER_TONES[voiceover_tone]["label"]
    try:
        script_result = write_city_script_with_deepseek(
            city=guess_destination(title),
            image_count=image_count,
            duration_per_image=duration,
            tone_label=tone_label,
        )
        subtitles = [str(line).strip() for line in script_result.get("subtitles", []) if str(line).strip()]
        voiceover_lines = [str(line).strip() for line in script_result.get("voiceover", []) if str(line).strip()]
        if len(subtitles) != image_count or len(voiceover_lines) != image_count:
            raise RuntimeError("DeepSeek 返回的字幕或旁白数量和图片数量不一致。")

        lines = voiceover_lines
        data["subtitles"] = subtitles
        data["xiaohongshu_title"] = str(script_result.get("xiaohongshu_title", "")).strip()
        data["video_description"] = str(script_result.get("video_description", "")).strip()
        data["title"] = str(script_result.get("title", title)).strip() or title
        source_text = "DeepSeek"
    except Exception as exc:
        lines = generate_voiceover_lines(title, image_count, duration, voiceover_tone)
        data["subtitles"] = lines
        data["title"] = title
        source_text = f"本地规则（DeepSeek 不可用：{exc}）"

    data["duration_per_image"] = duration
    data["aspect_ratio"] = "16:9"
    data["voiceover_tone"] = voiceover_tone
    data["voiceover"] = "\n".join(lines)
    save_script(data)

    total_seconds = image_count * duration
    flash(f"已通过{source_text}按“{tone_label}”语气，根据 {image_count} 张图片和每张 {duration} 秒，生成 {len(lines)} 句旁白和字幕。预计视频约 {total_seconds} 秒。")
    return redirect(url_for("index"))


@app.route("/generate_smart_script", methods=["POST"])
def generate_smart_script():
    ensure_dirs()
    data = load_script()
    title = request.form.get("title", data.get("title", "慢慢抵达一座城")).strip()
    duration = int(request.form.get("duration_per_image", data.get("duration_per_image", 6)))
    voiceover_tone = request.form.get("voiceover_tone", data.get("voiceover_tone", "healing"))
    if voiceover_tone not in VOICEOVER_TONES:
        voiceover_tone = "healing"

    image_files = get_image_files()
    if not image_files:
        flash("请先上传图片，再根据图片内容生成旁白和字幕。")
        return redirect(url_for("index"))

    try:
        tone_label = VOICEOVER_TONES[voiceover_tone]["label"]
        image_analysis = analyze_images_with_qwen_vl(image_files)
        script_result = write_script_with_deepseek(
            image_analysis=image_analysis,
            city=guess_destination(title),
            duration_per_image=duration,
            tone_label=tone_label,
        )
        subtitles = [str(line).strip() for line in script_result.get("subtitles", []) if str(line).strip()]
        voiceover_lines = [str(line).strip() for line in script_result.get("voiceover", []) if str(line).strip()]
        if not subtitles or not voiceover_lines:
            raise RuntimeError("DeepSeek 没有生成可用字幕或旁白。")

        data["title"] = str(script_result.get("title", title)).strip() or title
        data["duration_per_image"] = duration
        data["aspect_ratio"] = "16:9"
        data["voiceover_tone"] = voiceover_tone
        data["subtitles"] = subtitles
        data["voiceover"] = "\n".join(voiceover_lines)
        data["xiaohongshu_title"] = str(script_result.get("xiaohongshu_title", "")).strip()
        data["video_description"] = str(script_result.get("video_description", "")).strip()
        data["image_order_advice"] = str(script_result.get("image_order_advice", "")).strip()
        data["image_analysis"] = image_analysis
        save_script(data)
        flash(f"已用 Qwen-VL 分析 {len(image_files)} 张图片，并由 DeepSeek 生成标题、旁白、字幕、小红书标题和简介。")
    except Exception as exc:
        lines = generate_voiceover_lines(title, len(image_files), duration, voiceover_tone)
        data["title"] = title
        data["duration_per_image"] = duration
        data["aspect_ratio"] = "16:9"
        data["voiceover_tone"] = voiceover_tone
        data["subtitles"] = lines
        data["voiceover"] = "\n".join(lines)
        save_script(data)
        flash(f"视觉 API 不可用，已回退到本地规则生成。原因：{exc}")

    return redirect(url_for("index"))


@app.route("/upload", methods=["POST"])
def upload():
    ensure_dirs()
    files = request.files.getlist("images")
    saved = 0
    current_index = next_image_index()

    for file in files:
        if not file or not file.filename:
            continue
        suffix = Path(file.filename).suffix.lower()
        if suffix not in ALLOWED_IMAGE_EXTS:
            continue

        filename = f"{current_index:02d}{suffix}"
        file.save(IMAGE_DIR / filename)
        saved += 1
        current_index += 1

    if saved:
        flash(f"已上传 {saved} 张图片。")
    else:
        flash("没有上传成功的图片，请选择 jpg、jpeg 或 png 文件。")
    return redirect(url_for("index"))


@app.route("/delete_image", methods=["POST"])
def delete_image():
    ensure_dirs()
    filename = request.form.get("filename", "")
    image_path = image_path_from_name(filename)

    if image_path and image_path.exists():
        image_path.unlink()
        flash(f"已移除图片：{image_path.name}")
    else:
        flash("移除失败：没有找到这张图片。")

    return redirect(url_for("index"))


@app.route("/clear_images", methods=["POST"])
def clear_images():
    ensure_dirs()
    deleted = 0

    for image_file in IMAGE_DIR.iterdir():
        if image_file.suffix.lower() in ALLOWED_IMAGE_EXTS:
            image_file.unlink()
            deleted += 1

    flash(f"已清空图片，共移除 {deleted} 张。")
    return redirect(url_for("index"))


@app.route("/upload_bgm", methods=["POST"])
def upload_bgm():
    ensure_dirs()
    file = request.files.get("bgm")
    if file and file.filename:
        file.save(AUDIO_DIR / "bgm.mp3")
        flash("背景音乐已上传：audio/bgm.mp3")
    else:
        flash("没有选择背景音乐文件。")
    return redirect(url_for("index"))


@app.route("/generate_scene_topics", methods=["POST"])
def generate_scene_topics():
    destination = request.form.get("destination", "").strip()
    aspect_ratio = request.form.get("aspect_ratio", "16:9").strip() or "16:9"
    timing_plan = plan_video_timing(request.form.get("video_duration", "60"))
    style_keywords = request.form.get("style_keywords", "").strip()
    negative_keywords = request.form.get("negative_keywords", "").strip()
    scenes_text = request.form.get("scenes_text", "").strip()
    current_scenes = [line.strip() for line in scenes_text.splitlines() if line.strip()]

    if not destination:
        return jsonify({"ok": False, "error": "请先输入目的地/主题。"}), 400

    try:
        scenes = generate_scene_topics_with_api(
            destination=destination,
            aspect_ratio=aspect_ratio,
            style_keywords=style_keywords,
            negative_keywords=negative_keywords,
            count=timing_plan["image_count"],
            current_scenes=current_scenes,
            video_duration=timing_plan["video_duration"],
            duration_per_image=timing_plan["duration_per_image"],
        )
        scenes = fit_scene_count(scenes, timing_plan["image_count"], destination)
        return jsonify({"ok": True, "scenes": scenes, "source": "DeepSeek", "timing_plan": timing_plan})
    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": str(exc),
            "fallback_scenes": fit_scene_count(generate_default_scenes(destination), timing_plan["image_count"], destination),
            "timing_plan": timing_plan,
            "source": "local_fallback",
        }), 200


@app.route("/generate_prompts", methods=["POST"])
def generate_prompts():
    ensure_dirs()
    destination = request.form.get("destination", "泉州古城").strip() or "泉州古城"
    timing_plan = plan_video_timing(request.form.get("video_duration", "60"))
    aspect_ratio = request.form.get("aspect_ratio", "16:9").strip() or "16:9"
    style_keywords = request.form.get("style_keywords", default_prompt_form()["style_keywords"]).strip()
    negative_keywords = request.form.get("negative_keywords", default_prompt_form()["negative_keywords"]).strip()
    scenes_text = request.form.get("scenes_text", "").strip()

    scenes = [line.strip() for line in scenes_text.splitlines() if line.strip()]
    if not scenes:
        scenes = generate_default_scenes(destination)
    scenes = fit_scene_count(scenes, timing_plan["image_count"], destination)

    use_api = request.form.get("use_api") == "on"
    if use_api:
        try:
            scenes = generate_scene_topics_with_api(
                destination=destination,
                aspect_ratio=aspect_ratio,
                style_keywords=style_keywords,
                negative_keywords=negative_keywords,
                count=timing_plan["image_count"],
                current_scenes=scenes,
                video_duration=timing_plan["video_duration"],
                duration_per_image=timing_plan["duration_per_image"],
            )
            scenes = fit_scene_count(scenes, timing_plan["image_count"], destination)
            prompt_output = generate_prompt_markdown_with_api(
                destination=destination,
                aspect_ratio=aspect_ratio,
                style_keywords=style_keywords,
                negative_keywords=negative_keywords,
                scenes=scenes,
                timing_plan=timing_plan,
            )
            if "城市运镜策略" not in prompt_output:
                prompt_output = (
                    f"## 城市运镜策略\n\n"
                    f"{motion_strategy_for_destination(destination)}\n\n"
                    f"---\n\n"
                    f"{prompt_output}"
                )
            flash("已调用文本 API 增强生成图片提示词，并保存到 IMAGE_PROMPTS.md。")
        except Exception as exc:
            prompt_output = build_prompt_document(
                destination=destination,
                aspect_ratio=aspect_ratio,
                style_keywords=style_keywords,
                negative_keywords=negative_keywords,
                scenes=scenes,
                timing_plan=timing_plan,
            )
            flash(f"文本 API 不可用，已回退到本地规则生成提示词。原因：{exc}")
    else:
        prompt_output = build_prompt_document(
            destination=destination,
            aspect_ratio=aspect_ratio,
            style_keywords=style_keywords,
            negative_keywords=negative_keywords,
            scenes=scenes,
            timing_plan=timing_plan,
        )
        flash("图片提示词已生成，并保存到 IMAGE_PROMPTS.md。")
    PROMPTS_FILE.write_text(prompt_output, encoding="utf-8")

    data = load_script()
    data["title"] = destination
    data["duration_per_image"] = max(2, int(round(timing_plan["duration_per_image"])))
    data["planned_video_duration"] = timing_plan["video_duration"]
    data["planned_image_count"] = timing_plan["image_count"]
    save_script(data)

    prompt_form = {
        "destination": destination,
        "video_duration": timing_plan["video_duration"],
        "image_count": timing_plan["image_count"],
        "planned_duration_per_image": timing_plan["duration_per_image"],
        "aspect_ratio": aspect_ratio,
        "style_keywords": style_keywords,
        "negative_keywords": negative_keywords,
        "scenes_text": "\n".join(scenes)
    }

    return render_template("index.html", **build_view_data(prompt_output=prompt_output, prompt_form=prompt_form))


@app.route("/generate", methods=["POST"])
def generate():
    ensure_dirs()
    try:
        result = subprocess.run(
            [sys.executable, "main.py"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "未知错误")[-1500:]
            flash("生成失败：\n" + error_text)
        else:
            flash("视频生成成功：output/final.mp4")
    except Exception as exc:
        flash(f"生成失败：{exc}")
    return redirect(url_for("index"))


@app.route("/download")
def download():
    if not OUTPUT_FILE.exists():
        flash("还没有生成视频。")
        return redirect(url_for("index"))
    return send_file(OUTPUT_FILE, as_attachment=True)


if __name__ == "__main__":
    ensure_dirs()
    app.run(host="127.0.0.1", port=5000, debug=True)
