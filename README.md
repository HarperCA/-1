# AI 旅游图片伪视频自动生成器

这个项目用于把多张旅游图片自动合成为短视频，适合做小红书、抖音、视频号、B站横屏展示的慢节奏文旅风格视频。

## 功能

- 自动读取 `images/` 文件夹中的图片
- 自动裁剪为统一画幅
- 自动添加推近、拉远、横移、上移、下移运镜效果
- 自动添加中文字幕
- 自动生成 AI 中文旁白配音：`audio/voice.mp3`
- 可选添加背景音乐：`audio/bgm.mp3`
- 自动混合旁白与背景音乐
- 导出 `output/final.mp4`

## 项目结构

```text
.
├─ main.py
├─ requirements.txt
├─ script.json
├─ IMAGE_PROMPTS.md
├─ .gitignore
├─ images/
│  └─ .gitkeep
├─ audio/
│  └─ .gitkeep
├─ output/
│  └─ .gitkeep
└─ scripts/
   └─ generate_demo_images.py
```

## 安装依赖

```bash
pip install -r requirements.txt
```

## 快速运行

### 1. 生成示例图片

```bash
python scripts/generate_demo_images.py
```

### 2. 生成视频

```bash
python main.py
```

运行时会自动生成 AI 旁白：

```text
audio/voice.mp3
```

最终视频会在：

```text
output/final.mp4
```

## 替换成自己的旅游图片

把图片放进 `images/` 文件夹，命名建议：

```text
01.jpg
02.jpg
03.jpg
04.jpg
05.jpg
06.jpg
07.jpg
08.jpg
```

然后运行：

```bash
python main.py
```

## 添加背景音乐

把背景音乐放到：

```text
audio/bgm.mp3
```

如果有背景音乐，程序会自动把背景音乐音量压低，并和 AI 旁白混合。

## 修改字幕和旁白

打开 `script.json`。

- `subtitles`：画面底部字幕
- `voiceover`：AI 旁白朗读文本

如果没有单独写 `voiceover`，程序会把 `subtitles` 合并成旁白。

## 16:9 横屏导出

如果你要做横屏 16:9 视频，打开 `main.py`，把：

```python
VIDEO_W = 1080
VIDEO_H = 1920
```

改成：

```python
VIDEO_W = 1920
VIDEO_H = 1080
```

然后重新运行：

```bash
python main.py
```

## 真实摄影图片提示词

仓库里的 `IMAGE_PROMPTS.md` 已经提供了泉州古城 16:9 真实摄影风格图片提示词。

使用方式：

1. 打开即梦、豆包、通义万相或可灵图片。
2. 复制 `IMAGE_PROMPTS.md` 中的提示词生成图片。
3. 下载后命名为 `01.jpg` 到 `08.jpg`。
4. 放入 `images/` 文件夹。
5. 运行 `python main.py`。

## 说明

这是低成本版本，不调用云端 AI 视频接口。它的核心思路是：

```text
图片 + 自动运镜 + 字幕 + AI旁白 + 背景音乐 = 旅游伪视频
```

适合你的电脑配置，也适合后续继续升级为图形界面版或自动生成图片版。