# AI 旅游图片伪视频自动生成器

这个项目用于把多张旅游图片自动合成为竖屏短视频，适合做小红书、抖音、视频号的慢节奏文旅风格视频。

## 功能

- 自动读取 `images/` 文件夹中的图片
- 自动裁剪为 9:16 竖屏
- 自动添加推近、拉远、横移、上移、下移运镜效果
- 自动添加中文字幕
- 可选添加背景音乐
- 导出 `output/final.mp4`

## 项目结构

```text
.
├─ main.py
├─ requirements.txt
├─ script.json
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

生成的视频会在：

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

如果没有背景音乐，程序也能运行，只是导出视频没有音乐。

## 修改字幕

打开 `script.json`，修改 `subtitles` 里面的文字即可。

## 说明

这是低成本版本，不调用云端 AI 视频接口。它的核心思路是：

```text
图片 + 自动运镜 + 字幕 + 音乐 = 旅游伪视频
```

适合你的电脑配置，也适合后续继续升级为自动配音版、图形界面版。