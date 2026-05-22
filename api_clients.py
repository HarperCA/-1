import base64
import html
import json
import os
import re
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path


def _extract_text_prompt_blocks(markdown):
    prompts = []
    in_block = False
    block_lines = []
    for raw_line in (markdown or "").splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            if in_block:
                prompt = "\n".join(block_lines).strip()
                if prompt:
                    prompts.append(prompt)
                block_lines = []
                in_block = False
            else:
                in_block = line.lower() == "```text"
            continue
        if in_block:
            block_lines.append(raw_line)
    return prompts


def _strip_html(text):
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def search_destination_web_context(destination, max_results=5):
    query = f"{destination} 旅游 景点 建筑 风貌 真实地点"
    results = []
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query, "kl": "cn-zh"})
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"联网检索失败：HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"联网检索失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("联网检索超时，请稍后重试。") from exc

    def add_result(title, snippet, result_url):
        title = _strip_html(title)[:120]
        snippet = _strip_html(snippet)[:260]
        if result_url.startswith("//duckduckgo.com/l/?"):
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse("https:" + result_url).query)
            result_url = parsed.get("uddg", [result_url])[0]
        if title and snippet and not any(item["title"] == title for item in results):
            results.append({"title": title, "snippet": snippet, "url": result_url})

    ddg_blocks = re.findall(r'<div[^>]+class="[^"]*result[^"]*"[^>]*>([\s\S]*?)(?=<div[^>]+class="[^"]*result|</body>)', html, flags=re.I)
    for block in ddg_blocks:
        title_match = re.search(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>', block, flags=re.I)
        snippet_match = re.search(r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>|<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet2>.*?)</div>', block, flags=re.I)
        if title_match and snippet_match:
            add_result(title_match.group("title"), snippet_match.group("snippet") or snippet_match.group("snippet2") or "", title_match.group("url"))
        if len(results) >= max_results:
            break

    if len(results) < max_results:
        bing_url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "setlang": "zh-CN"})
        bing_request = urllib.request.Request(
            bing_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            },
        )
        try:
            with urllib.request.urlopen(bing_request, timeout=12) as response:
                bing_html = response.read().decode("utf-8", errors="ignore")
            for block in re.findall(r'<li[^>]+class="b_algo"[^>]*>([\s\S]*?)</li>', bing_html, flags=re.I):
                title_match = re.search(r'<h2[^>]*>\s*<a[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>\s*</h2>', block, flags=re.I)
                snippet_match = re.search(r'<p[^>]*>(?P<snippet>.*?)</p>', block, flags=re.I)
                if title_match and snippet_match:
                    add_result(title_match.group("title"), snippet_match.group("snippet"), title_match.group("url"))
                if len(results) >= max_results:
                    break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            pass

    if not results:
        raise RuntimeError("联网检索没有返回可用结果。")

    return {
        "query": query,
        "results": results,
        "summary": "\n".join(f"- {item['title']}：{item['snippet']}" for item in results),
    }


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


def get_openai_image_settings():
    return {
        "label": "OpenAI 图片生成",
        "base_url": os.getenv("OPENAI_API_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        "model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5"),
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "key_env": "OPENAI_API_KEY",
        "size": os.getenv("OPENAI_IMAGE_SIZE", "1536x1024"),
        "quality": os.getenv("OPENAI_IMAGE_QUALITY", "medium"),
    }


def _env_int(name, default):
    value = os.getenv(name, str(default)).strip()
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是整数，当前值是 {value!r}。") from exc


def _env_float(name, default):
    value = os.getenv(name, str(default)).strip()
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是数字，当前值是 {value!r}。") from exc


def get_local_image_settings():
    return {
        "label": "本地图片模型",
        "backend": os.getenv("LOCAL_IMAGE_BACKEND", "sd-webui").strip().lower(),
        "base_url": os.getenv("LOCAL_IMAGE_BASE_URL", "http://127.0.0.1:7860").rstrip("/"),
        "width": _env_int("LOCAL_IMAGE_WIDTH", 1280),
        "height": _env_int("LOCAL_IMAGE_HEIGHT", 720),
        "steps": _env_int("LOCAL_IMAGE_STEPS", 28),
        "cfg_scale": _env_float("LOCAL_IMAGE_CFG_SCALE", 7),
        "sampler_name": os.getenv("LOCAL_IMAGE_SAMPLER", "DPM++ 2M Karras"),
        "seed": _env_int("LOCAL_IMAGE_SEED", -1),
        "negative_prompt": os.getenv(
            "LOCAL_IMAGE_NEGATIVE_PROMPT",
            "text, watermark, logo, blurry, low quality, cartoon, illustration, distorted face, deformed body",
        ),
    }


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
    parts.append("生成视频时会烧录字幕，并使用系统语音合成旁白。")
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
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{settings['label']} 网络连接失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"{settings['label']} 请求超时，请稍后重试。") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{settings['label']} 返回了无法解析的 JSON。") from exc

    try:
        return result["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"{settings['label']} 返回格式异常：{str(result)[:600]}") from exc


def _chat_completion(messages, model, temperature=0.7, max_tokens=1600):
    return _chat_completion_with_settings(get_text_api_settings(), messages, model, temperature, max_tokens)


def generate_image_with_openai(prompt, size=None, quality=None):
    settings = get_openai_image_settings()
    if not settings["api_key"]:
        raise RuntimeError(f"没有配置 OpenAI API Key，请在 .env 中设置 {settings['key_env']}。")
    if not settings["base_url"]:
        raise RuntimeError("没有配置 OpenAI API Base URL。")

    payload = {
        "model": settings["model"],
        "prompt": prompt,
        "n": 1,
        "size": size or settings["size"],
        "quality": quality or settings["quality"],
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{settings['base_url']}/images/generations",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings['api_key']}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI 生图失败：HTTP {exc.code} {detail[:600]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI 网络连接失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("OpenAI 生图请求超时，请稍后重试。") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenAI 返回了无法解析的 JSON。") from exc

    try:
        item = result["data"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"OpenAI 返回格式异常：{str(result)[:600]}") from exc

    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])

    image_url = item.get("url")
    if image_url:
        try:
            with urllib.request.urlopen(image_url, timeout=180) as response:
                return response.read()
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI 图片下载失败：{exc.reason}") from exc

    raise RuntimeError(f"OpenAI 没有返回可保存的图片数据：{str(result)[:600]}")


def generate_image_with_local_model(prompt):
    settings = get_local_image_settings()
    if settings["backend"] not in {"sd-webui", "automatic1111"}:
        raise RuntimeError("当前只内置支持 Stable Diffusion WebUI / Automatic1111 的 txt2img API。")
    if not settings["base_url"]:
        raise RuntimeError("没有配置本地图片模型地址，请设置 LOCAL_IMAGE_BASE_URL。")

    payload = {
        "prompt": prompt,
        "negative_prompt": settings["negative_prompt"],
        "width": settings["width"],
        "height": settings["height"],
        "steps": settings["steps"],
        "cfg_scale": settings["cfg_scale"],
        "sampler_name": settings["sampler_name"],
        "seed": settings["seed"],
        "batch_size": 1,
        "n_iter": 1,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{settings['base_url']}/sdapi/v1/txt2img",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"本地图片模型生图失败：HTTP {exc.code} {detail[:600]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接本地图片模型：{exc.reason}。请确认 WebUI 已用 --api 启动，地址为 {settings['base_url']}。") from exc
    except TimeoutError as exc:
        raise RuntimeError("本地图片模型生图请求超时，请稍后重试。") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("本地图片模型返回了无法解析的 JSON。") from exc

    images = result.get("images") or []
    if not images:
        raise RuntimeError(f"本地图片模型没有返回图片数据：{str(result)[:600]}")

    image_data = images[0]
    if "," in image_data and image_data.lstrip().startswith("data:"):
        image_data = image_data.split(",", 1)[1]
    try:
        return base64.b64decode(image_data)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("本地图片模型返回的图片不是有效 Base64 数据。") from exc


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
    mime_by_ext = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }
    mime = mime_by_ext.get(path.suffix.lower(), "application/octet-stream")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"



def _profile_key(destination):
    text = destination or ""
    if any(word in text for word in ["海", "岛", "湾", "沙滩", "海岸"]):
        return "coastal"
    if any(word in text for word in ["草原", "牧场", "旷野"]):
        return "grassland"
    if any(word in text for word in ["雪山", "冰川", "高原"]):
        return "mountain"
    if any(word in text for word in ["湖", "江", "河", "溪", "水乡"]):
        return "water"
    if any(word in text for word in ["山", "峡谷", "森林", "瀑布"]):
        return "nature"
    if any(word in text for word in ["古城", "古镇", "老城", "老街"]):
        return "old_town"
    if any(word in text for word in ["寺", "庙", "宫", "塔", "石窟"]):
        return "heritage"
    if any(word in text for word in ["城市", "都市", "街区", "夜景"]):
        return "city"
    return "travel"


def _generic_profile_for_key(destination, key):
    base = {
        "coastal": {
            "terms": f"{destination}的海岸线、海面、礁石、沙滩、港口或渔村、海风、日落、旅人背影",
            "avoid": "与海岸主题无关的内陆古城模板、错误地标、夸张摆拍、乱码招牌",
            "topics": ["海岸清晨开场", "沿海道路引入", "海面与礁石远景", "浪花纹理细节", "旅人背影看海", "港口生活气息", "日落海面金光", "海岸留白收尾"],
        },
        "grassland": {
            "terms": f"{destination}的草原、远山、牧道、牛羊或马群、毡房或营地、风、云影、日落",
            "avoid": "城市街巷模板、密集建筑、错误民俗、夸张摆拍",
            "topics": ["草原远景开场", "牧道引入空间", "远山云影", "草浪纹理细节", "旅人背影看草原", "牧场生活气息", "日落金色草场", "旷野留白收尾"],
        },
        "mountain": {
            "terms": f"{destination}的雪山或高山、山脊、云雾、冰川或岩壁、栈道、旅人背影、日照金山或暮色",
            "avoid": "低海拔城市模板、错误地标、卡通雪山、过度滤镜",
            "topics": ["山脉晨光开场", "栈道引入空间", "雪峰核心远景", "岩壁云雾细节", "旅人背影望山", "山间生活痕迹", "金色山脊", "暮色雪山收尾"],
        },
        "water": {
            "terms": f"{destination}的水面、岸线、桥或步道、倒影、小舟或行人、晨雾、柳岸或江岸、傍晚水色",
            "avoid": "无关古城模板、错误地标、过度商业街、乱码招牌",
            "topics": ["水面晨光开场", "岸线步道引入", "核心水景远景", "水波倒影细节", "旅人背影看水", "岸边生活气息", "傍晚水面金光", "水色留白收尾"],
        },
        "nature": {
            "terms": f"{destination}的自然地貌、山林、溪谷、道路或观景台、树影、云雾、旅人背影、傍晚光线",
            "avoid": "城市街巷模板、错误地标、人工痕迹过重、过度滤镜",
            "topics": ["自然远景开场", "山路引入空间", "核心风景展开", "树影纹理细节", "旅人背影入景", "在地生活痕迹", "傍晚光影升起", "远山留白收尾"],
        },
        "old_town": {
            "terms": f"{destination}的街巷、门楼、屋檐、石板路、地方小店、在地生活、灯火、历史质感",
            "avoid": "自然山水模板、错误地标、现代商业感过重、乱码招牌",
            "topics": ["古城清晨开场", "街口引入空间", "核心街巷画面", "屋檐石板细节", "旅人背影穿行", "市井生活气息", "傍晚灯火初上", "巷口余韵收尾"],
        },
        "heritage": {
            "terms": f"{destination}的历史建筑、院落、塔或殿宇、纹样细节、香火或人流、光影、静穆氛围",
            "avoid": "错误宗教符号、错误地标、夸张人群、乱码牌匾",
            "topics": ["历史空间开场", "入口轴线引入", "核心建筑远景", "纹样材质细节", "旅人背影仰望", "院落生活气息", "傍晚光影升起", "建筑留白收尾"],
        },
        "city": {
            "terms": f"{destination}的城市天际线、道路、街区、地标、玻璃反射、街头生活、夜色灯光",
            "avoid": "不属于当地的历史或自然地标、空洞城市模板、过度赛博风",
            "topics": ["城市远景开场", "道路引入空间", "核心地标画面", "街区材质细节", "旅人背影穿行", "街头生活气息", "夜色灯光升起", "天际线收尾"],
        },
        "travel": {
            "terms": f"{destination}真实可辨认的自然、人文、街区或地标特征，地方生活，旅人背影，傍晚收尾",
            "avoid": "与目的地不符的模板化元素、错误地标、乱码招牌、夸张摆拍",
            "topics": ["清晨远景开场", "抵达空间引入", "核心画面展开", "地方细节纹理", "旅人背影代入", "在地生活气息", "傍晚情绪升起", "留白远景收尾"],
        },
    }
    return base.get(key, base["travel"])


def _profile(destination):
    key = _profile_key(destination)
    profile = _generic_profile_for_key(destination, key)
    topics = profile["topics"]
    return {
        "title": f"{destination}慢旅行",
        "terms": profile["terms"],
        "avoid": profile["avoid"],
        "topics": topics,
        "subtitles": [
            f"清晨的光，慢慢照亮{destination}。",
            "第一眼风景，把脚步轻轻放慢。",
            "空间向前展开，旅程也有了方向。",
            "细节藏在光影里，真实而动人。",
            "旅人走进画面，也走进这段风景。",
            "生活的声音，让这里更有温度。",
            "傍晚的光落下，情绪慢慢升起。",
            f"最后一眼，把{destination}留在心里。",
        ],
        "voiceover": [
            f"清晨的光慢慢照亮{destination}，一段慢旅行从这里开始。",
            "第一眼风景铺开的时候，脚步也自然放慢了下来。",
            "空间一点点向前展开，让画面有了继续行走的方向。",
            "那些不经意的细节藏在光影里，安静却很动人。",
            "旅人走进画面，也把我们带进这段真实的风景。",
            "生活的声音从身边经过，让这里不只是风景，也有温度。",
            "傍晚的光落下来，整段旅程的情绪也慢慢升起。",
            f"最后一眼留给远处，也把{destination}的余韵留在心里。",
        ],
    }


def _char_limits(duration_per_image):
    try:
        duration = float(duration_per_image)
    except (TypeError, ValueError):
        duration = 6
    if duration <= 3:
        return 8, 14
    if duration <= 5:
        return 12, 20
    if duration <= 6:
        return 18, 28
    if duration <= 8:
        return 24, 36
    return 30, 45


def _normalize_sentence(text):
    text = re.sub(r"\s+", "", str(text or "").strip())
    text = text.strip("，,、；;：:")
    if not text:
        return ""
    if text[-1] in "，,、；;：:":
        text = text[:-1]
    if text[-1] not in "。！？!?":
        text += "。"
    return text


def _fit_count(lines, count, fallback_lines):
    normalized = []
    used = set()
    for line in lines:
        line = _normalize_sentence(line)
        if not line or line in used:
            continue
        used.add(line)
        normalized.append(line)
        if len(normalized) == count:
            break

    fallback_index = 0
    while len(normalized) < count:
        candidate = _normalize_sentence(fallback_lines[fallback_index % len(fallback_lines)])
        fallback_index += 1
        if candidate in used:
            candidate = candidate[:-1] + f"，第{len(normalized) + 1}幕。"
        used.add(candidate)
        normalized.append(candidate)

    return normalized[:count]


def _local_script(destination, image_count, duration_per_image, tone_label="治愈文艺"):
    profile = _profile(destination)
    subtitles = _fit_count(profile["subtitles"], image_count, profile["subtitles"])
    voiceover = _fit_count(profile["voiceover"], image_count, profile["voiceover"])
    return {
        "title": profile["title"],
        "subtitles": subtitles,
        "voiceover": voiceover,
        "xiaohongshu_title": f"{profile['title']}｜把脚步交给风景",
        "video_description": f"这是一支以{destination}为主题的{tone_label}旅行短片，用连续镜头从开场、抵达、细节到傍晚收尾，呈现目的地的视觉气质。",
        "image_order_advice": "建议按照清晨开场、核心风景、地方细节、旅人背影、生活气息、傍晚收尾的顺序排列图片。",
    }


def _validate_script_result(result, destination, image_count, duration_per_image, tone_label):
    if not isinstance(result, dict):
        return _local_script(destination, image_count, duration_per_image, tone_label)

    fallback = _local_script(destination, image_count, duration_per_image, tone_label)
    subtitles = _fit_count(result.get("subtitles", []), image_count, fallback["subtitles"])
    voiceover = _fit_count(result.get("voiceover", []), image_count, fallback["voiceover"])

    title = _normalize_sentence(result.get("title", "")).rstrip("。") or fallback["title"]
    return {
        "title": title,
        "subtitles": subtitles,
        "voiceover": voiceover,
        "xiaohongshu_title": str(result.get("xiaohongshu_title") or fallback["xiaohongshu_title"]).strip(),
        "video_description": str(result.get("video_description") or fallback["video_description"]).strip(),
        "image_order_advice": str(result.get("image_order_advice") or fallback["image_order_advice"]).strip(),
    }


def _build_script_messages(destination, image_count, duration_per_image, tone_label, image_analysis=None):
    min_chars, max_chars = _char_limits(duration_per_image)
    profile = _profile(destination)
    analysis_text = json.dumps(image_analysis or [], ensure_ascii=False, indent=2)
    analysis_rule = (
        "可参考图片分析里的画面类型，但不要逐张机械复述 visual_summary。"
        "要把这些图片组织成一支完整旅行短片。"
        if image_analysis else
        "没有图片分析时，请根据目的地真实特征自行组织连续镜头叙事。"
    )
    user_prompt = f"""
请为一支旅游短片生成字幕文案和旁白文本。

地点/主题：{destination}
地点关键词：{profile["terms"]}
禁止错配元素：{profile["avoid"]}
图片数量 image_count：{image_count}
每张图片持续时间 duration_per_image：{duration_per_image} 秒
语气风格 tone_label：{tone_label}
图片分析参考：
{analysis_text}

核心任务：
写一支完整的旅游短片脚本，不是描述图片，不是说明书。
{analysis_rule}

叙事顺序必须推进：
开场 → 抵达 → 环境 → 细节 → 人间烟火/地方生活 → 情绪升起 → 傍晚/收尾 → 余韵。
如果 image_count 多于 8，就在这些段落之间自然扩展，不能重复句子或换词复读。
旁白必须像跟着同一条旅行路线走：每一句对应同序号图片的时间、地点和画面功能；不要写成抽象散文，也不要让旁白内容与图片提示词的空间顺序冲突。

硬性要求：
1. subtitles 数量必须严格等于 {image_count}。
2. voiceover 数量必须严格等于 {image_count}。
3. 每一句必须独立完整，并以句号、问号或感叹号结束。
4. 不允许残缺句，不允许以逗号、顿号、分号、冒号结尾。
5. 不允许出现完全重复的句子。
6. 不允许连续多句使用相同句式。
7. 每句建议 {min_chars}-{max_chars} 个汉字，duration_per_image 越短越精炼。
8. 地点必须匹配。你必须根据用户输入的目的地自行判断真实自然、人文、地标和地方生活元素；不得套用与目的地无关的古城、海岸、草原、都市或山水模板。
9. 字幕 subtitles 更短，更像画面文字；旁白 voiceover 略完整，更适合朗读，但两者不能矛盾。
10. 字幕会烧录进视频画面，旁白会用系统语音合成为音轨。
11. 时间必须连续：不要在相邻句子里从清晨跳到夜晚再跳回午后。
12. 地点移动必须合理：从远景/入口/道路进入核心地点，再到细节、人物、生活、傍晚和收尾，不要随机换地点。
13. 每句旁白必须能直接搭配同序号图片，如果画面是细节，旁白就写细节的触感；如果画面是收尾，旁白就写余韵。

必须严格返回 JSON，不要 Markdown，不要解释：
{{
  "title": "{destination}慢旅行",
  "subtitles": ["第1句", "第2句"],
  "voiceover": ["第1句", "第2句"],
  "xiaohongshu_title": "标题",
  "video_description": "80字以内简介",
  "image_order_advice": "图片顺序建议"
}}
""".strip()

    system_prompt = (
        "你是资深文旅宣传片导演和旅行短片编剧。"
        "你的任务不是描述图片，而是写一支有连续镜头感、旅行叙事和视觉享受的短片脚本。"
        "你特别注意地点匹配、句子完整、避免重复、避免机械说明。"
        "你只返回严格 JSON。"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _lens_plan_for_index(index, total):
    roles = [
        "开场建立镜头",
        "引入空间镜头",
        "核心识别镜头",
        "细节纹理镜头",
        "人物代入镜头",
        "生活文化镜头",
        "情绪升起镜头",
        "收尾留白镜头",
    ]
    shots = ["远景", "全景", "中远景", "近景", "中景", "中近景", "中远景", "远景"]
    times = ["清晨", "上午", "午后", "午后", "下午", "傍晚前", "傍晚", "暮色将近"]
    compositions = ["开阔留白构图", "前景引导构图", "三分法构图", "层次感构图", "背影代入构图", "生活场景构图", "侧逆光构图", "大面积留白构图"]
    lights = ["清晨柔光", "上午自然光", "午后通透天光", "柔和散射光", "干净侧光", "傍晚前的暖光", "金色时刻侧逆光", "晚霞余光或蓝调天光"]
    moods = ["安静、通透、建立期待", "松弛、有进入感", "开阔、有识别度", "细腻、真实、有触感", "自然、有代入感", "温暖、有生活气", "温柔、情绪升起", "安静、留白、有余韵"]
    motions = ["zoom_in", "pan_right", "zoom_in", "still", "pan_left", "pan_right", "zoom_in", "zoom_out"]

    if total <= 1:
        pos = 0
    else:
        pos = round(index / max(1, total - 1) * 7)
    pos = max(0, min(7, pos))
    return {
        "role": roles[pos],
        "shot": shots[pos],
        "time": times[pos],
        "composition": compositions[pos],
        "light": lights[pos],
        "mood": moods[pos],
        "motion": motions[pos],
    }


def _motion_reason(motion, shot, topic):
    if motion == "zoom_in":
        return f"适合从{shot}缓慢推进到{topic}，增强进入感和观看焦点"
    if motion == "zoom_out":
        return f"适合从主体慢慢拉远，留下环境和余韵，作为收束镜头"
    if motion == "pan_left":
        return f"适合横向扫过画面层次，让旅人视线或空间关系自然展开"
    if motion == "pan_right":
        return f"适合顺着道路、水面或街景横移，制造连续行进感"
    if motion == "pan_up":
        return f"适合表现高耸主体或纵向空间，从细节带到整体"
    if motion == "pan_down":
        return f"适合从天空、山体或建筑上部落到地面生活细节"
    return f"适合保持稳定画面，让细节和真实质感自然呈现"


def _voiceover_intent_for_role(role, destination):
    mapping = {
        "开场建立镜头": f"交代{destination}的整体气质，让观众知道旅程从哪里开始",
        "引入空间镜头": "让观众产生走进目的地的感觉，承接开场并进入具体空间",
        "核心识别镜头": "点出最有代表性的画面，让目的地变得可识别",
        "细节纹理镜头": "用材质、光影和局部细节放慢节奏，增强真实感",
        "人物代入镜头": "用旅人背影或侧影建立代入感，但不抢走风景主体",
        "生活文化镜头": "补充地方生活和人文温度，让画面不只是风景",
        "情绪升起镜头": "把时间推进到傍晚或金色时刻，让情绪变柔和",
        "收尾留白镜头": f"回望{destination}，用安静远景结束整段旅程",
    }
    return mapping.get(role, "承接上一镜头，并为下一镜头保留自然过渡")




def _looks_like_default_scene_list(topics):
    defaults = {"城市清晨远景", "老街巷入口", "地标建筑外观", "地方建筑细节", "街头生活氛围", "本地文化元素", "游客背影慢行", "傍晚收尾远景"}
    return bool(topics) and sum(1 for item in topics if item in defaults) >= max(2, len(topics) // 2)


def _prompt_topics_for_destination(destination, scenes, count):
    profile = _profile(destination)
    topics = [str(scene).strip() for scene in (scenes or []) if str(scene).strip()]
    if not topics or _looks_like_default_scene_list(topics):
        topics = profile["topics"]
        if count <= len(topics):
            if count <= 1:
                return [topics[0]]
            return [topics[round(index / max(1, count - 1) * (len(topics) - 1))] for index in range(count)]
    return [topics[index % len(topics)] for index in range(count)]


def _local_prompt_markdown(destination, aspect_ratio, style_keywords, negative_keywords, scenes, image_count=None):
    profile = _profile(destination)
    count = int(image_count or len(scenes) or 8)
    topics = _prompt_topics_for_destination(destination, scenes, count)
    route_places = []
    for index, topic in enumerate(topics, start=1):
        plan = _lens_plan_for_index(index - 1, count)
        route_places.append({
            "index": index,
            "time": plan["time"],
            "place": topic,
            "role": plan["role"],
            "voiceover_intent": _voiceover_intent_for_role(plan["role"], destination),
        })

    lines = [
        f"# {destination}旅游伪视频镜头提示词方案｜{aspect_ratio}版",
        "",
        "## 整体分镜策略",
        f"- 目的地识别：围绕{profile['terms']}展开，避免{profile['avoid']}。",
        "- 时间连续性：同一支短片必须发生在一个合理的旅行时间线里，按清晨、上午、午后、下午、傍晚、暮色自然推进，不能一会儿白天一会儿夜晚乱跳。",
        "- 地点路线：镜头必须像一次真实游览路线，遵循远景建立、进入路径、核心地点、周边细节、人物停留、地方生活、傍晚回望、收尾留白的空间递进。",
        "- 视频节奏：开场建立环境 → 引入空间 → 核心画面 → 细节纹理 → 旅人代入 → 生活文化 → 情绪升起 → 收尾留白；前后镜头的方向、时间、天气和人物状态要能接上。",
        "- 切镜原则：远景与中近景交替，空镜与人物背影交替，前一镜头的空间方向要能自然接到后一镜头，不能只是孤立地点拼贴。",
        "- 旁白对应：每个镜头都要有一句旁白意图，图片主体必须能承接这句旁白的情绪和信息。",
        "- 运镜原则：开阔远景用慢推进或横移，线性空间用横移，高耸主体用上摇，细节镜头用静止或轻微推进，收尾镜头用慢拉远。",
        "- 统一视觉：真实摄影、电影感纪录片风格、自然光、真实细节、色彩统一、干净画面、适合图片运镜短视频。",
        "- 人物规则：只出现远景、侧影或背影，人物不抢主体，不出现清晰正脸。",
        "",
        "## 旅行路线与旁白对应表",
        "| 镜头 | 时间推进 | 地点/空间 | 镜头作用 | 旁白意图 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in route_places:
        lines.append(f"| {item['index']:02d} | {item['time']} | {item['place']} | {item['role']} | {item['voiceover_intent']} |")
    lines.extend([
        "",
    ]
    )

    for index, topic in enumerate(topics, start=1):
        plan = _lens_plan_for_index(index - 1, count)
        if any(word in topic for word in ["塔", "楼", "墙", "山峰", "雪山", "高楼"]):
            plan = {**plan, "motion": "pan_up" if plan["shot"] in ["中景", "中近景", "近景"] else plan["motion"]}
        if plan["role"] == "细节纹理镜头":
            plan = {**plan, "motion": "still"}
        motion_note = _motion_reason(plan["motion"], plan["shot"], topic)
        previous_place = "无，作为开场建立目的地整体环境" if index == 1 else topics[index - 2]
        next_place = "无，作为收尾留白" if index == count else topics[index]
        voiceover_intent = _voiceover_intent_for_role(plan["role"], destination)
        prompt = (
            f"镜头{index:02d}，{plan['role']}，{plan['time']}的{destination}真实摄影{plan['shot']}，"
            f"画面主体是{topic}，必须基于{destination}真实可验证的地理、人文或风景特征，"
            f"这是旅行路线中的第{index}站，上一镜头来自{previous_place}，下一镜头将去往{next_place}，"
            f"时间、天气、光线和人物状态必须与前后镜头连续；"
            f"本镜头对应旁白意图：{voiceover_intent}，画面必须能支撑这句旁白，不要出现与旁白无关的主体；"
            f"参考方向：{profile['terms']}；避免出现{profile['avoid']}；"
            f"构图采用{plan['composition']}，主体位置明确，前景/中景/背景层次清楚，"
            f"画面中保留可做运镜的边缘空间，避免主体贴边或过度居中；"
            f"光线为{plan['light']}，整体氛围{plan['mood']}，色彩自然统一，"
            f"与上一镜头形成自然切镜衔接，与下一镜头保持同一季节、天气和旅行节奏；"
            f"推荐运镜：{plan['motion']}，{motion_note}；"
            f"真实摄影，电影感，纪录片风格，自然光，高清真实细节，干净画面，"
            f"适合图片运镜短视频，{aspect_ratio}横屏，{style_keywords}；"
            f"负面约束：{negative_keywords}，避免文字，避免水印，避免Logo，避免AI感，"
            "避免卡通感，避免插画风，避免畸形建筑，避免错误地标，避免乱码招牌，"
            "避免人物脸部畸形，避免清晰正脸，避免夸张摆拍，避免过度滤镜。"
        )
        lines.extend([
            f"## {index:02d}.jpg {topic}",
            "",
            f"- 镜头作用：{plan['role']}",
            f"- 景别：{plan['shot']}",
            f"- 时间：{plan['time']}",
            f"- 地点/空间：{topic}",
            f"- 上一镜头承接：{previous_place}",
            f"- 下一镜头去向：{next_place}",
            f"- 旁白意图：{voiceover_intent}",
            f"- 推荐运镜：{plan['motion']}，{motion_note}",
            "- 切镜衔接：与前后镜头保持同一地点气质、统一色调、天气状态、时间推进和旅行节奏，避免画面风格跳变。",
            "",
            "```text",
            prompt,
            "```",
            "",
        ])
    return "\n".join(lines)


def _markdown_is_structurally_valid(markdown, image_count):
    required = ["整体分镜策略", "旅行路线与旁白对应表", "镜头作用", "景别", "时间", "地点/空间", "上一镜头承接", "下一镜头去向", "旁白意图", "推荐运镜", "切镜衔接", "```text"]
    return all(item in markdown for item in required) and len(_extract_text_prompt_blocks(markdown)) == image_count


def _review_prompt_markdown_with_api(settings, destination, image_count, markdown):
    review_prompt = f"""
请审查下面这份旅游伪视频图片提示词方案是否合格。只返回 JSON，不要解释。

目的地：{destination}
镜头数量要求：{image_count}

审查标准：
1. 是否准确适配目的地，而不是套用别的城市/自然/古城模板。
2. 是否有 {image_count} 个镜头，且每个镜头都有 text 代码块。
3. 是否每个镜头都有镜头作用、景别、时间、推荐运镜、切镜衔接。
4. 是否包含“旅行路线与旁白对应表”，并明确每个镜头的时间推进、地点/空间、镜头作用和旁白意图。
5. 是否每个镜头都有上一镜头承接、下一镜头去向、旁白意图，且时间、地点和人物状态合理连续。
6. 是否像一条真实游览路线，而不是孤立地点或素材拼贴。
7. 是否包含真实摄影、电影感、纪录片风格、自然光、真实细节、色彩统一、16:9横屏、负面约束。
8. 是否有明显重复、地点错配、错误地标、AI感、时间跳变、空间跳变或过度模板化。

返回格式：
{{"ok": true, "issues": []}}
或
{{"ok": false, "issues": ["问题1", "问题2"]}}

待审查内容：
{markdown[:12000]}
""".strip()
    response = _chat_completion_with_settings(
        settings=settings,
        messages=[
            {"role": "system", "content": "你是严格的文旅短视频分镜审稿人，只检查目的地匹配、镜头连续性、格式完整性和真实摄影可用性。你只返回 JSON。"},
            {"role": "user", "content": review_prompt},
        ],
        model=settings["model"],
        temperature=0.2,
        max_tokens=700,
    )
    result = _extract_json(response)
    if not isinstance(result, dict):
        return {"ok": False, "issues": ["自检没有返回对象"]}
    return {"ok": bool(result.get("ok")), "issues": [str(item) for item in result.get("issues", [])]}


def _build_prompt_markdown_messages(destination, aspect_ratio, style_keywords, negative_keywords, clean_scenes, image_count, timing_plan, reference_analysis=None, web_context=None, repair_issues=None, previous_markdown=""):
    scenes_text = "\n".join(f"{index + 1}. {scene}" for index, scene in enumerate(clean_scenes))
    reference_text = json.dumps(reference_analysis or [], ensure_ascii=False, indent=2)
    reference_rule = ""
    if reference_analysis:
        reference_rule = f"""
现实参考图片分析（来自 Qwen-VL，仅用于约束画面，不要机械复述）：
{reference_text}

参考图使用规则：
- 必须优先吸收参考图里看得见的现实主体、材质、色调、构图、天气、光线、空间氛围和适合运镜。
- 参考图不能证明的地标、城市和年代，不要凭空写死；可以写成“与参考图一致的街巷/建筑/器物/自然风景气质”。
- 如果参考图与地点/主题冲突，以用户输入的地点/主题为主，但保留参考图的真实摄影风格、画面结构和可见元素。
- 生成的每条图片提示词都要体现“真实参考图约束”，减少泛化模板感和随机想象。
""".strip()
    web_context_text = ""
    if web_context and web_context.get("summary"):
        web_context_text = f"""
联网检索到的真实地点参考（来自普通网页搜索，可能不完整，需要谨慎使用）：
{web_context.get("summary")}

联网参考使用规则：
- 优先吸收检索结果里明确出现的真实景点、建筑、街区、自然地貌、地方文化和空间风貌。
- 不要把检索摘要写成旁白说明，也不要在提示词中提到“搜索结果”。
- 搜索资料与用户上传参考图冲突时，以用户主题和参考图可见内容为主；搜索资料只用于避免地点错配和泛化模板。
- 不确定的信息不要写死成唯一地标，可以转写为“具有当地特征的街区/建筑/水岸/山景/文化细节”。
""".strip()
    repair_text = ""
    if repair_issues:
        repair_text = "\n上一次生成未通过审查，必须修正这些问题：\n" + "\n".join(f"- {issue}" for issue in repair_issues)
        if previous_markdown:
            repair_text += "\n不要重复上一次的错误，必要时重写镜头主题和画面主体。"

    user_prompt = f"""
请生成 IMAGE_PROMPTS.md 的完整 Markdown 内容。不要解释，不要输出 JSON。

你不是在写普通图片提示词。你要像“旅游短视频分镜设计师 + 真实摄影图片提示词写作者”一样，设计一套可用于“图片 + 运镜 = 伪视频”的镜头提示词方案。

你必须自己根据目的地判断真实地理、人文、风景、地标、季节、光线和地方气质。不要依赖代码里的固定地点模板；如果用户给的镜头主题明显泛化或与目的地不匹配，你要主动改写成符合目的地的连续镜头。

基础信息：
地点/主题：{destination}
图片数量：{image_count}
每张图片停留：{timing_plan.get("duration_per_image", "自动")} 秒
画幅：{aspect_ratio}
统一风格关键词：{style_keywords}
统一负面约束：{negative_keywords}
用户给的镜头主题，仅供参考，可改写：
{scenes_text}
{reference_rule}
{web_context_text}
{repair_text}

整体分镜逻辑：
1. 开场建立镜头：交代地点整体环境，远景/全景，适合慢推进或横移。
2. 引入空间镜头：街口、江边、入口、道路、桥、渡口、步道等，让观众产生进入感。
3. 核心地标/核心画面：最具识别度的画面，必须准确属于目的地。
4. 细节镜头：建筑细节、自然纹理、水波、屋檐、石板路、树影、地方器物等。
5. 人物代入镜头：旅人背影、侧影、远景行走或观看风景，不要清晰正脸。
6. 生活感/烟火气/在地文化：摊位、日常、非遗、地方生活，但不能抢主题。
7. 情绪升起镜头：傍晚、金色时刻、灯光初上、江面晚霞、晚风等。
8. 收尾镜头：安静、留白、有余韵，适合视频结尾。
如果图片数量不是 8，要按这个叙事逻辑自然压缩或扩展，绝不能简单重复。

连续性硬性要求：
- 必须先设计一条“旅行路线与旁白对应表”，再写每个镜头提示词。
- 时间必须连续推进：清晨 → 上午 → 午后 → 下午 → 傍晚 → 暮色，不允许同一组镜头白天、夜晚、雨天、晴天混乱跳变。
- 地点必须合理移动：从远景建立到入口/道路/步道，再到核心地点、周边细节、人物停留、地方生活、傍晚回望、收尾留白。
- 每个镜头必须说明“上一镜头承接”和“下一镜头去向”，前后地点要像真实游览路线，不能随机跳到无关地点。
- 每个镜头必须说明“旁白意图”，图片主体必须服务这句旁白要表达的信息或情绪。
- 若后续第 4 步生成旁白，旁白会按这些镜头顺序写，所以你必须让每张图天然能对应一段旁白。

每一个镜头必须明确写出：镜头编号、镜头作用、景别、推荐运镜、时间信息、地点/空间、上一镜头承接、下一镜头去向、旁白意图、画面主体、构图方式、光线与氛围、画面情绪、切镜衔接。

运镜选择规则：
- 山水远景、江面、湖面、开阔风景：zoom_in 或 pan_right。
- 横向街景、桥、道路、江岸、湖岸：pan_left 或 pan_right。
- 高耸建筑、塔、城墙、山体仰视：pan_up。
- 水波、屋檐、石板、器物、食物等细节：still 或轻微 zoom_in。
- 人物背影行走：pan_left / pan_right，跟随行进方向。
- 收尾空镜、开阔远景：zoom_out。
不要乱给运镜，不要每条都给同一个运镜。

每条可复制提示词必须统一包含：真实摄影、电影感、纪录片风格、自然光、真实细节、色彩统一、适合图片运镜短视频、{aspect_ratio} 横屏、高清、干净画面。

每条可复制提示词必须包含负面约束：避免文字、避免水印、避免Logo、避免AI感、避免卡通感、避免插画风、避免畸形建筑、避免错误地标、避免乱码招牌、避免人物脸部畸形、避免清晰正脸、避免夸张摆拍、避免过度滤镜。

输出格式必须严格使用 Markdown：
# {destination}旅游伪视频镜头提示词方案｜{aspect_ratio}版

## 整体分镜策略
- 用 4 到 6 条 bullet 说明整体切镜、时间、景别、情绪、色调和运镜策略。
- 必须说明时间如何连续推进、地点如何合理移动、旁白如何对应画面。

## 旅行路线与旁白对应表
| 镜头 | 时间推进 | 地点/空间 | 镜头作用 | 旁白意图 |
| --- | --- | --- | --- | --- |
| 01 | 清晨 | 具体地点/空间 | 开场建立镜头 | 旁白要表达什么 |

## 01.jpg 镜头标题

- 镜头作用：...
- 景别：...
- 时间：...
- 地点/空间：...
- 上一镜头承接：...
- 下一镜头去向：...
- 旁白意图：...
- 推荐运镜：zoom_in / zoom_out / pan_left / pan_right / pan_up / pan_down / still，并说明原因
- 切镜衔接：承接上一镜头什么，下一镜头如何过渡，时间和空间为什么合理

```text
完整、详细、真实、可直接复制的中文图片提示词。必须包含本镜头的时间、地点、上一镜头承接、下一镜头去向、旁白意图和画面主体。必须是一整段，不要拆成项目符号。
```

一直生成到 {image_count:02d}.jpg，数量必须准确，不多不少。
""".strip()
    return [
        {
            "role": "system",
            "content": (
                "你是旅游短视频分镜设计师 + 真实摄影图片提示词写作者。"
                "你的任务不是描述孤立图片，而是设计一套能拼成同一支旅游伪视频的连续镜头。"
                "你必须自己理解任意目的地的真实特征，并考虑切镜顺序、运镜方式、景别变化、时间推进、情绪推进、色调统一、人物出现方式和收尾留白。"
                "不要依赖固定城市模板，不要地点错配，不要写空泛关键词。"
            ),
        },
        {"role": "user", "content": user_prompt},
    ]


def generate_prompt_markdown_with_api(destination, aspect_ratio, style_keywords, negative_keywords, scenes, timing_plan=None, reference_analysis=None, web_context=None):
    timing_plan = timing_plan or {}
    image_count = int(timing_plan.get("image_count") or len(scenes) or 8)
    clean_scenes = _prompt_topics_for_destination(destination, scenes, image_count)
    settings = get_text_api_settings()

    if not settings["available"]:
        if reference_analysis:
            raise RuntimeError(f"参考图提示词生成需要 DeepSeek 文本 API，请在 .env 中设置 {settings['key_env']}。")
        return _local_prompt_markdown(destination, aspect_ratio, style_keywords, negative_keywords, clean_scenes, image_count)

    markdown = ""
    issues = []
    try:
        for attempt in range(2):
            messages = _build_prompt_markdown_messages(
                destination=destination,
                aspect_ratio=aspect_ratio,
                style_keywords=style_keywords,
                negative_keywords=negative_keywords,
                clean_scenes=clean_scenes,
                image_count=image_count,
                timing_plan=timing_plan,
                reference_analysis=reference_analysis,
                web_context=web_context,
                repair_issues=issues if attempt else None,
                previous_markdown=markdown,
            )
            markdown = _chat_completion(
                messages=messages,
                model=settings["model"],
                temperature=0.68,
                max_tokens=max(4200, image_count * 560),
            )
            if not _markdown_is_structurally_valid(markdown, image_count):
                issues = [f"格式不完整：必须有整体分镜策略、每镜头元信息和 {image_count} 个 text 代码块"]
                continue
            try:
                review = _review_prompt_markdown_with_api(settings, destination, image_count, markdown)
            except Exception:
                return markdown
            if review["ok"]:
                return markdown
            issues = review["issues"] or ["目的地匹配或镜头连续性不足"]
        if markdown and _markdown_is_structurally_valid(markdown, image_count):
            return markdown
        if reference_analysis:
            raise RuntimeError("DeepSeek 未能生成结构完整的参考图提示词。")
        return _local_prompt_markdown(destination, aspect_ratio, style_keywords, negative_keywords, clean_scenes, image_count)
    except Exception:
        if markdown and _markdown_is_structurally_valid(markdown, image_count):
            return markdown
        if reference_analysis:
            raise
        return _local_prompt_markdown(destination, aspect_ratio, style_keywords, negative_keywords, clean_scenes, image_count)


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
    profile = _profile(destination)
    if not settings["available"]:
        return _prompt_topics_for_destination(destination, current_scenes or [], count)

    current_scenes_text = "\n".join(current_scenes or [])
    user_prompt = f"""
请根据目的地真实特征，策划 {count} 个旅游短片镜头主题。

目的地/主题：{destination}
视频总时长：{video_duration or "未指定"} 秒
计划每张图片停留：{duration_per_image or "自动"} 秒
画幅：{aspect_ratio}
风格关键词：{style_keywords}
负面约束：{negative_keywords}
当前镜头主题，仅供参考；如果明显泛化或与目的地不匹配，请主动重写：
{current_scenes_text}

要求：
1. 你必须自己判断目的地真实的自然、人文、街区、地标和生活元素，不要套固定城市模板。
2. 必须是一条连续视觉路线：开场、进入、核心画面、细节、人物代入、生活文化、情绪升起、收尾。
3. 每个主题 4 到 14 个中文字符，具体、可拍、可生图。
4. 不要输出空泛词，比如“美丽风景”“地方细节”“地标建筑”这类泛称。
5. 只输出 JSON：{{"scenes":["主题1","主题2"]}}
""".strip()

    response = _chat_completion_with_settings(
        settings=settings,
        messages=[
            {"role": "system", "content": "你是中文文旅短视频分镜导演，擅长为任意目的地设计准确、连续、可拍摄的镜头主题。你只返回 JSON。"},
            {"role": "user", "content": user_prompt},
        ],
        model=settings["model"],
        temperature=0.7,
        max_tokens=1000,
    )
    result = _extract_json(response)
    scenes = result.get("scenes", []) if isinstance(result, dict) else result
    scenes = [str(scene).strip() for scene in scenes if str(scene).strip()]
    if len(scenes) < max(3, min(count, 3)):
        return _prompt_topics_for_destination(destination, current_scenes or profile["topics"], count)
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
            {"role": "system", "content": "你是旅游短视频剪辑师，能根据图片内容判断画面类型和适合运镜。"},
            {"role": "user", "content": content},
        ],
        model=settings["model"],
        temperature=0.45,
        max_tokens=2600,
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
    image_count = len(image_analysis)
    if not settings["available"]:
        return _local_script(city, image_count, duration_per_image, tone_label)

    messages = _build_script_messages(city, image_count, duration_per_image, tone_label, image_analysis=image_analysis)
    for _ in range(2):
        response = _chat_completion_with_settings(
            settings=settings,
            messages=messages,
            model=settings["model"],
            temperature=0.66,
            max_tokens=max(2200, image_count * 160),
        )
        result = _validate_script_result(_extract_json(response), city, image_count, duration_per_image, tone_label)
        return result
    return _local_script(city, image_count, duration_per_image, tone_label)
def write_city_script_with_deepseek(city, image_count, duration_per_image, tone_label):
    settings = get_text_api_settings()
    image_count = max(1, int(image_count or 8))
    if not settings["available"]:
        return _local_script(city, image_count, duration_per_image, tone_label)

    messages = _build_script_messages(city, image_count, duration_per_image, tone_label)
    for _ in range(2):
        response = _chat_completion_with_settings(
            settings=settings,
            messages=messages,
            model=settings["model"],
            temperature=0.68,
            max_tokens=max(2200, image_count * 160),
        )
        result = _validate_script_result(_extract_json(response), city, image_count, duration_per_image, tone_label)
        return result
    return _local_script(city, image_count, duration_per_image, tone_label)
