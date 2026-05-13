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


def generate_prompt_markdown_with_api(destination, aspect_ratio, style_keywords, negative_keywords, scenes):
    settings = get_text_api_settings()
    scenes_text = "\n".join(f"{index + 1}. {scene}" for index, scene in enumerate(scenes))
    user_prompt = f"""
请为旅游短视频项目生成图片提示词文档。只生成 Markdown，不要调用或提及任何生图 API。

目的地/主题：{destination}
画幅：{aspect_ratio}
统一风格关键词：{style_keywords}
统一负面约束：{negative_keywords}
镜头主题：
{scenes_text}

要求：
1. 每个镜头输出一条可直接复制到图片工具的中文提示词。
2. 提示词要体现城市地标、季节/时间、景别、光线、真实摄影质感和人物安全约束。
3. 人物只允许远景、侧影或背影，不要清晰面部。
4. 不要写任何代码，不要输出 JSON。
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
    user_prompt = f"""
请根据 Qwen-VL 的图片分析结果，为文旅伪视频重新组织脚本。

主题城市：{city}
每张图片时长：{duration_per_image} 秒
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
1. 字幕短、贴画面，适合直接压在视频里。
2. 旁白自然，10 到 34 个汉字，适合 edge-tts 朗读。
3. 不要凭空编造视觉模型没有提到的具体地标。
4. 如果图片顺序已经合理，就说明可以直接使用。
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
    user_prompt = f"""
请为城市文旅伪视频生成完整脚本。

主题城市：{city}
图片数量：{image_count}
每张图片时长：{duration_per_image} 秒
旁白语气：{tone_label}

必须只输出 JSON 对象，字段为：
title: 视频标题
xiaohongshu_title: 小红书标题
video_description: 视频简介，80 字以内
subtitles: 字符串数组，长度必须等于图片数量
voiceover: 字符串数组，长度必须等于图片数量

要求：
1. 适合古城、老街、寺庙、建筑、生活感、旅人背影、傍晚收尾这类文旅画面。
2. 字幕短、贴画面；旁白自然，10 到 34 个汉字，适合 edge-tts 朗读。
3. 不调用、不提及图片生成 API。
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
