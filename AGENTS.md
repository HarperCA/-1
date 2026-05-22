# AGENTS.md

本文件给后续在本仓库工作的 AI 编码代理使用。请先阅读这里，再修改代码。

## 项目概览

这是一个本地 Flask 旅游短视频生成工具。用户通过网页上传旅行图片，生成 AI 文案、图片提示词和最终的无字幕、无声音 MP4 视频。

核心模块：

- `app.py`：Flask Web 应用、页面路由、上传/删除图片、文案生成、视频生成任务、历史记录和下载接口。
- `main.py`：命令行视频生成入口，读取 `images/` 和 `script.json`，输出 `output/final.mp4`。
- `api_clients.py`：DeepSeek 文本 API、Qwen-VL 图片理解 API，以及 OpenAI 兼容接口调用逻辑。
- `run_server.py`：生产式本地启动入口，默认端口 `5000`，也可传入端口参数。
- `video_templates.py`、`shot_sorter.py`：图片镜头顺序、运镜模板和辅助规则。
- `templates/index.html`：单页前端界面。

## 常用命令

建议在虚拟环境中安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

启动 Web 应用：

```powershell
python run_server.py
```

指定端口启动：

```powershell
python run_server.py 8080
```

仅用命令行生成视频：

```powershell
python main.py
```

当前仓库没有专门的测试套件。改动后至少运行相关入口，确认 Flask 可启动、图片上传/读取路径正常，必要时用少量图片实际生成一次视频。

## 环境变量

本项目会从仓库根目录的 `.env` 读取本地配置，但 `.env` 已被 `.gitignore` 忽略，不能提交真实密钥。

常用变量：

- `DEEPSEEK_API_KEY`：用于 AI 文案生成。
- `DEEPSEEK_API_BASE_URL`：可选，默认 `https://api.deepseek.com/v1`。
- `DEEPSEEK_TEXT_MODEL`：可选，默认 `deepseek-chat`。
- `DASHSCOPE_API_KEY`：用于 Qwen-VL 图片理解和提示词生成。
- `QWEN_API_BASE_URL`：可选，默认 `https://dashscope.aliyuncs.com/compatible-mode/v1`。
- `QWEN_VL_MODEL`：可选，默认 `qwen-vl-plus`。

不要把 API Key、密钥文件或用户私人素材写进版本库。

## 目录和生成产物

这些目录包含用户素材、缓存或生成结果，通常不应提交实际内容：

- `images/`：用户上传的图片，仅保留需要的占位或示例时才考虑提交。
- `audio/`：音频素材目录，当前视频生成默认不输出声音。
- `output/`：当前生成结果，例如 `output/final.mp4` 和进度文件。
- `history/`：历史生成记录和历史视频。
- `exports/`：用户复制或导出的结果。
- `generated_cache/`：运行时缓存，只提交目录结构和 `.gitkeep`。

`.gitignore` 已忽略绝大多数生成物、日志、本地密钥和缓存文件。修改忽略规则时要小心，避免把大文件或隐私数据纳入 Git。

## 编码约定

- 保持 Python 代码简单直接，优先使用标准库和现有依赖，不随意引入新框架。
- 路径统一使用 `pathlib.Path`，并优先基于 `BASE_DIR` 解析仓库内路径。
- JSON 文件读写使用 `encoding="utf-8"`，写出中文内容时保留 `ensure_ascii=False`。
- API 响应保持 `api_ok(...)` / `api_error(...)` 的结构：`ok`、`message`、`data`。
- 处理用户传入的文件名、导出目录、历史视频 ID 时，必须继续保留路径安全检查，不能允许目录穿越。
- 视频生成是耗时任务，Web 入口使用后台线程和 `generation_lock` 控制并发；不要把长任务直接阻塞在请求线程里。
- 如果修改上传、删除、导出、历史清理等文件操作，优先限制在对应项目目录下，并避免递归删除不受控路径。
- 项目里有部分早期文件存在中文乱码注释或输出。新代码请使用正常 UTF-8 中文；修复乱码时只改必要范围，避免顺手重写无关逻辑。

## 前端约定

- `templates/index.html` 是主界面，改动时保持和现有 API 路由一致。
- 用户操作应给出清楚的成功、失败和进度状态。
- 上传、生成、下载、复制/移动、历史记录等核心流程不能因为视觉调整而退化。
- 如果新增前端请求，请在 `app.py` 中提供对应 JSON 接口，并保持错误信息可读。

## 视频生成注意事项

- 输出目标默认为 `output/final.mp4`。
- 支持图片扩展名以 `app.py` / `main.py` 中的 `ALLOWED_IMAGE_EXTS` 为准，修改时要同步考虑上传校验和生成逻辑。
- 当前设计说明里明确：生成视频不显示字幕，也不输出声音。除非需求明确要求，别默认加入字幕或音频。
- `script.json` 记录标题、每张图片时长、旁白和字幕等脚本文案；视频生成主要使用 `duration_per_image`。

## 提交前检查

完成改动后建议检查：

- `python run_server.py` 能启动。
- 主页可访问，核心接口返回 JSON。
- 没有提交 `.env`、日志、缓存、用户图片、音频、MP4 或大型生成文件。
- 若改动视频生成逻辑，至少用 1-3 张小图片跑通一次 `python main.py` 或 Web 生成流程。
