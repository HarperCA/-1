import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path


OPENAI_COMPATIBLE_PRESETS = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "text_model": "gpt-4.1-mini",
        "vision_model": "gpt-4.1-mini",
        "key_env": "OPENAI_API_KEY",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "text_model": "deepseek-chat",
        "vision_model": "",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "label": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "text_model": "qwen-plus",
        "vision_model": "qwen-vl-plus",
        "key_env": "DASHSCOPE_API_KEY",
    },
    "doubao": {
        "label": "豆包 Ark",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "text_model": "doubao-seed-1-6-250615",
        "vision_model": "doubao-seed-1-6-vision-250815",
        "key_env": "ARK_API_KEY",
    },
    "custom": {
        "label": "自定义 OpenAI 兼容接口",
        "base_url": "",
        "text_model": "",
        "vision_model": "",
        "key_env": "LLM_API_KEY",
    },
}


def load_local_env():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_local_env()


def _settings_from_preset(provider, key_override="", base_override="", model_override="", vision=False):
    preset = OPENAI_COMPATIBLE_PRESETS.get(provider, OPENAI_COMPATIBLE_PRESETS[provider if provider in OPENAI_COMPATIBLE_PRESETS else "openai"])
    key_env = preset["key_env"]
    api_key = key_override or os.getenv(key_env, "")
    model_key = "vision_model" if vision else "text_model"

    return {
        "provider": provider,
        "label": preset["label"],
        "base_url": (base_override or preset["base_url"]).rstrip("/"),
        "model": model_override or preset[model_key] or preset["text_model"],
        "text_model": model_override or preset["text_model"],
        "vision_model": model_override or preset["vision_model"] or preset["text_model"],
        "api_key": api_key,
        "key_env": key_env,
        "available": bool(api_key),
    }


def get_text_api_settings():
    return _settings_from_preset(
        provider="deepseek",
        key_override=os.getenv("DEEPSEEK_API_KEY", ""),
        base_override=os.getenv("DEEPSEEK_API_BASE_URL", "https://api.deepseek.com/v1"),
        model_override=os.getenv("DEEPSEEK_TEXT_MODEL", "deepseek-chat"),
        vision=False,
    )


def get_vision_api_settings():
    return _settings_from_preset(
        provider="qwen",
        key_override=os.getenv("DASHSCOPE_API_KEY", ""),
        base_override=os.getenv("QWEN_API_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        model_override=os.getenv("QWEN_VL_MODEL", "qwen-vl-plus"),
        vision=True,
    )


def get_api_settings():
    text_settings = get_text_api_settings()
    vision_settings = get_vision_api_settings()
    return {
        "provider": "deepseek+qwen-vl",
        "label": "DeepSeek + Qwen-VL",
        "text_model": text_settings["model"],
        "vision_model": vision_settings["model"],
        "available": text_settings["available"] or vision_settings["available"],
        "text_available": text_settings["available"],
        "vision_available": vision_settings["available"],
        "text_key_env": text_settings["key_env"],
        "vision_key_env": vision_settings["key_env"],
    }


def describe_api_status():
    text_settings = get_text_api_settings()
    vision_settings = get_vision_api_settings()
    parts = []
    parts.append("DeepSeek 文本 API 已就绪。" if text_settings["available"] else "DeepSeek 文本 API 未配置，请设置 DEEPSEEK_API_KEY。")
    parts.append("Qwen-VL 图片理解 API 已就绪。" if vision_settings["available"] else "Qwen-VL 图片理解 API 未配置，请设置 DASHSCOPE_API_KEY。")
    parts.append("语音合成继续使用本地 edge-tts。")
    return " ".join(parts)


def _chat_completion_with_settings(settings, messages, model, temperature=0.7, max_tokens=1600):
    if not settings["available"]:
        raise RuntimeError(f"没有配置 {settings['label']} API Key，请设置 {settings['key_env']}。")
    if not settings["base_url"]:
        raise RuntimeError(f"没有配置 {settings['label']} API Base URL。")
    if not model:
        raise RuntimeError(f"没有配置 {settings['label']} 模型名称。")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{settings['base_url']}/chat/completions",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings['api_key']}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"{settings['label']} 请求失败：HTTP {exc.code} {detail[:600]}") from exc

    try:
        return result["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"{settings['label']} 返回格式异常：{str(result)[:600]}") from exc


def _chat_completion(messages, model, temperature=0.7, max_tokens=1600):
    return _chat_completion_with_settings(get_text_api_settings(), messages, model, temperature, max_tokens)


def _extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if not match:
            raise
        return json.loads(match.group(1))


def _image_data_url(image_path):
    path = Path(image_path)
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def generate_prompt_markdown_with_api(destination, aspect_ratio, style_keywords, negative_keywords, scenes, timing_plan=None):
    settings = get_text_api_settings()
    timing_plan = timing_plan or {}
    scenes_text = "\n".join(f"{index + 1}. {scene}" for index, scene in enumerate(scenes))
    user_prompt = f"""
请为旅游短视频项目生成图片提示词文档。只生成 Markdown，不要调用或提及任何生图 API。

目的地/主题：{destination}
视频总时长：{timing_plan.get("video_duration", "未指定")} 秒
图片数量：{timing_plan.get("image_count", len(scenes))} 张
每张图片停留：{timing_plan.get("duration_per_image", "自动")} 秒
画幅：{aspect_ratio}
统一风格关键词：{style_keywords}
统一负面约束：{negative_keywords}
镜头主题：
{scenes_text}

要求：
1. 必须在文档开头输出一个 Markdown 小节，标题必须叫“## 城市运镜策略”。先判断目的地/主题的空间特征和城市气质，再说明本片整体适合的运镜方式。
   - 山水型目的地：航拍/高机位远景、水面低机位推进、竹筏/船只跟拍、倒影稳定悬停、山体仰拍。
   - 山城型目的地：高低落差俯拍、楼梯或坡道跟拍、建筑纵深推进、轻轨/道路横移、夜景拉远。
   - 古城/老街型目的地：巷道平视慢推、门窗框景、转角横向平移、建筑细节近景、生活烟火中景。
   - 园林/水乡型目的地：小桥水面平移、窗棂/树枝前景带入、对称构图悬停、曲径慢推。
   - 现代都市型目的地：天际线拉远、道路纵深推进、玻璃幕墙反射构图、江面/街区稳定横移。
2. 每个镜头输出一条可直接复制到图片工具的中文提示词。
3. 整组提示词必须像同一支连续短片的分镜，统一季节、天气、色彩、摄影质感、镜头语言和人物安全约束，不要每张图风格跳变。
4. 每个镜头都要明确写出：景别（远景/全景/中景/近景/特写）、拍摄角度（平视/低机位/高机位/俯拍/侧逆光等）、主体大小与画面位置、构图方式（三分法/对称/框景/引导线/前景遮挡）、前景/中景/背景层次、光线方向。
5. 每个镜头都要给出适合该城市/目的地的运镜方式，不能机械重复“推进/平移”。运镜要服务于空间特征，例如山水看层次和倒影、山城看高低落差、古城看巷道纵深、都市看天际线和反射。
6. 镜头顺序要有节奏：开场建立环境，中段切换地标/人文/细节，结尾有收束；远景、中景、近景、特写要交替安排，避免连续重复同一种构图。
7. 人物只允许远景、侧影或背影，不要清晰面部；人物大小要自然，不能抢占画面主体。
8. 不要写任何代码，不要输出 JSON。
""".strip()

    return _chat_completion(
        messages=[
            {"role": "system", "content": "你是文旅短视频导演和提示词策划，擅长真实摄影风格的城市镜头设计。"},
            {"role": "user", "content": user_prompt},
        ],
        model=settings["model"],
        temperature=0.75,
        max_tokens=2600,
    )


def generate_scene_topics_with_api(
    destination,
    aspect_ratio="16:9",
    style_keywords="",
    negative_keywords="",
    count=8,
    current_scenes=None,
    video_duration=0,
    duration_per_image=0,
):
    settings = get_text_api_settings()
    current_scenes_text = "\n".join(current_scenes or [])
    user_prompt = f"""
请根据用户输入的旅游目的地/主题，重新策划短视频镜头主题。

目的地/主题：{destination}
视频总时长：{video_duration or "未指定"} 秒
计划图片数量：{count} 张
计划每张图片停留：{duration_per_image or "自动"} 秒
画幅：{aspect_ratio}
风格关键词：{style_keywords}
负面约束：{negative_keywords}
当前镜头主题（仅供参考，不要机械改词）：
{current_scenes_text}

要求：
1. 必须结合目的地真实的自然、人文、城市或地标特征重新构思，不要套用“古城清晨、街巷、地标建筑”模板。
2. 必须输出 {count} 个镜头主题，每个主题 4 到 10 个中文字符，适合作为旅游短视频分镜标题。
3. 如果目的地是“桂林山水”，要体现漓江、喀斯特山峰、竹筏、象鼻山、阳朔、田园、渔火等地域特征。
4. 不要输出图片提示词，不要写解释。
5. 必须只输出 JSON，格式为：{{"scenes":["主题1","主题2"]}}
""".strip()

    response = _chat_completion_with_settings(
        settings=settings,
        messages=[
            {"role": "system", "content": "你是中文文旅短视频导演，擅长根据目的地真实特征设计镜头主题。"},
            {"role": "user", "content": user_prompt},
        ],
        model=settings["model"],
        temperature=0.78,
        max_tokens=900,
    )
    result = _extract_json(response)
    if isinstance(result, dict):
        scenes = result.get("scenes", [])
    else:
        scenes = result
    scenes = [str(scene).strip() for scene in scenes if str(scene).strip()]
    if len(scenes) < 3:
        raise RuntimeError("DeepSeek 没有返回可用的镜头主题。")
    return scenes[:count]


def analyze_images_with_qwen_vl(image_paths):
    settings = get_vision_api_settings()

    content = [
        {
            "type": "text",
            "text": (
                "请逐张分析这些用户上传的旅游图片。必须只输出 JSON 数组，数组顺序与图片顺序一致。"
                "每个元素包含：scene_type、visual_summary、best_motion、order_score、order_reason。"
                "scene_type 从古城、老街、寺庙、建筑细节、街头生活、人物背影、自然风景、傍晚夜景、其他中选择或概括。"
                "best_motion 从 zoom_in、zoom_out、pan_left、pan_right、pan_up、pan_down、still 中选择。"
                "visual_summary 用一句中文客观描述画面，不要虚构看不见的地标。order_score 为 1 到 10。"
            ),
        }
    ]
    for image_path in image_paths:
        content.append({"type": "image_url", "image_url": {"url": _image_data_url(image_path)}})

    response = _chat_completion_with_settings(
        settings=settings,
        messages=[
            {"role": "system", "content": "你是旅游短视频剪辑师，能根据图片内容写出匹配画面的字幕和旁白。"},
            {"role": "user", "content": content},
        ],
        model=settings["model"],
        temperature=0.55,
        max_tokens=2200,
    )
    items = _extract_json(response)
    if not isinstance(items, list):
        raise RuntimeError("视觉模型没有返回数组。")

    normalized = []
    for item in items[:len(image_paths)]:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "scene_type": str(item.get("scene_type", "")).strip(),
            "visual_summary": str(item.get("visual_summary", "")).strip(),
            "best_motion": str(item.get("best_motion", "zoom_in")).strip() or "zoom_in",
            "order_score": item.get("order_score", ""),
            "order_reason": str(item.get("order_reason", "")).strip(),
        })

    if not normalized:
        raise RuntimeError("Qwen-VL 没有生成可用图片分析。")

    return normalized


def write_script_with_deepseek(image_analysis, city, duration_per_image, tone_label):
    settings = get_text_api_settings()
    analysis_text = json.dumps(image_analysis, ensure_ascii=False, indent=2)
    max_voiceover_chars = max(10, min(38, int(float(duration_per_image) * 3.6)))
    max_subtitle_chars = max(8, min(24, int(float(duration_per_image) * 2.4)))
    user_prompt = f"""
请根据 Qwen-VL 的图片分析结果，为文旅伪视频重新组织脚本。

用户输入的目的地/主题：{city}
每张图片时长：{duration_per_image} 秒
字幕单句建议上限：{max_subtitle_chars} 个汉字
旁白单句建议上限：{max_voiceover_chars} 个汉字
旁白语气：{tone_label}
图片分析：
{analysis_text}

必须只输出 JSON 对象，字段为：
title: 视频标题
xiaohongshu_title: 小红书标题
video_description: 视频简介，80 字以内
subtitles: 字符串数组，一张图一句字幕
voiceover: 字符串数组，一张图一句旁白
image_order_advice: 对当前顺序的简短建议

要求：
1. title 必须根据用户输入的目的地/主题由 DeepSeek 重新生成，不能原样照抄“桂林”“泉州”这种地点词；标题要像成片标题，简洁、有传播感。
2. subtitles 和 voiceover 的数组长度必须等于图片分析数组长度，且一张图对应一句字幕和一句旁白。
3. 字幕短、贴画面，单句不超过 {max_subtitle_chars} 个汉字，适合直接压在视频里。
4. 旁白必须结合 Qwen-VL 对应图片的 visual_summary 生成，不能只写泛泛旅游文案；每句 10 到 {max_voiceover_chars} 个汉字，确保 edge-tts 朗读时长能落在每张图 {duration_per_image} 秒内。
5. 整体旁白要有开场、展开、转场、收束，前后语义连贯，不要每句互相孤立。
6. 不要凭空编造视觉模型没有提到的具体地标；如果要点出地名，只能使用用户输入目的地或图片分析中明确出现的信息。
7. image_order_advice 说明当前图片顺序是否适合叙事，如果不适合，给出简短调整建议。
""".strip()

    response = _chat_completion_with_settings(
        settings=settings,
        messages=[
            {"role": "system", "content": "你是中文文旅短视频编导，负责把看图结果组织成标题、字幕、旁白和简介。"},
            {"role": "user", "content": user_prompt},
        ],
        model=settings["model"],
        temperature=0.68,
        max_tokens=2200,
    )
    result = _extract_json(response)
    if not isinstance(result, dict):
        raise RuntimeError("DeepSeek 没有返回脚本对象。")
    return result


def write_city_script_with_deepseek(city, image_count, duration_per_image, tone_label):
    settings = get_text_api_settings()
    max_voiceover_chars = max(10, min(38, int(float(duration_per_image) * 3.6)))
    max_subtitle_chars = max(8, min(24, int(float(duration_per_image) * 2.4)))
    user_prompt = f"""
请为城市文旅伪视频生成完整脚本。

用户输入的目的地/主题：{city}
图片数量：{image_count}
每张图片时长：{duration_per_image} 秒
字幕单句建议上限：{max_subtitle_chars} 个汉字
旁白单句建议上限：{max_voiceover_chars} 个汉字
旁白语气：{tone_label}

必须只输出 JSON 对象，字段为：
title: 视频标题
xiaohongshu_title: 小红书标题
video_description: 视频简介，80 字以内
subtitles: 字符串数组，长度必须等于图片数量
voiceover: 字符串数组，长度必须等于图片数量

要求：
1. title 必须根据用户输入的目的地/主题重新生成，不能原样照抄地点词；标题要像成片标题。
2. subtitles 和 voiceover 的数组长度必须等于图片数量。
3. 字幕短、贴画面，单句不超过 {max_subtitle_chars} 个汉字。
4. 旁白自然，10 到 {max_voiceover_chars} 个汉字，适合 edge-tts 在每张图 {duration_per_image} 秒内读完。
5. 整体旁白要有开场、展开、转场、收束，前后语义连贯。
6. 不调用、不提及图片生成 API。
""".strip()

    response = _chat_completion_with_settings(
        settings=settings,
        messages=[
            {"role": "system", "content": "你是中文文旅短视频编导，擅长城市慢旅行脚本和小红书文案。"},
            {"role": "user", "content": user_prompt},
        ],
        model=settings["model"],
        temperature=0.72,
        max_tokens=1800,
    )
    result = _extract_json(response)
    if not isinstance(result, dict):
        raise RuntimeError("DeepSeek 没有返回脚本对象。")
    return result
