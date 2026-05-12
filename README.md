# AI 旅游图片伪视频自动生成器

这个项目用于把多张旅游图片自动合成为短视频，适合做小红书、抖音、视频号、B站的慢节奏文旅宣传片。

核心思路：

```text
图片 + 自动运镜 + 字幕 + AI旁白 + 背景音乐 = 旅游伪视频
```

项目默认导出 **16:9 横屏视频**，分辨率为：

```text
1920 x 1080
```

---

## 当前功能

- 自动读取 `images/` 文件夹中的图片
- 自动裁剪为统一 16:9 横屏画幅
- 自动添加推近、拉远、横移、上移、下移运镜效果
- 自动添加中文字幕
- 使用 `edge-tts` 自动生成中文 AI 旁白：`audio/voice.mp3`
- 支持背景音乐：`audio/bgm.mp3`
- 自动混合 AI 旁白和背景音乐
- 背景音乐自动循环、降低音量、淡入淡出
- 导出视频：`output/final.mp4`
- 支持 Flask 本地网页版操作

---

## 项目结构

```text
.
├─ main.py                         # 核心视频生成程序
├─ app.py                          # Flask 本地网页入口
├─ requirements.txt                # Python 依赖
├─ script.json                     # 视频标题、字幕、旁白配置
├─ IMAGE_PROMPTS.md                # 泉州古城图片生成提示词
├─ templates/
│  └─ index.html                   # 网页界面
├─ images/
│  └─ .gitkeep                    # 放图片，建议 01.jpg 到 08.jpg
├─ audio/
│  └─ .gitkeep                    # 放 bgm.mp3，程序生成 voice.mp3
├─ output/
│  └─ .gitkeep                    # 输出 final.mp4
└─ scripts/
   └─ generate_demo_images.py      # 生成横屏测试示例图
```

---

## 一、安装依赖

第一次运行前，在项目目录打开 CMD：

```bash
pip install -r requirements.txt
```

如果后续更新了代码，也可以重新运行一次这条命令。

---

## 二、命令行运行方式

### 1. 生成横屏示例图片

如果 `images/` 文件夹里没有图片，先运行：

```bash
python scripts/generate_demo_images.py
```

这会生成 8 张横屏测试图片：

```text
images/01.jpg
images/02.jpg
...
images/08.jpg
```

注意：这些只是测试图，不是真实旅游画面。真正做成片时，建议用 `IMAGE_PROMPTS.md` 里的提示词去即梦、豆包、通义万相、可灵图片等工具生成真实摄影风格图片。

### 2. 生成视频

```bash
python main.py
```

运行时会自动生成 AI 旁白：

```text
audio/voice.mp3
```

最终视频输出到：

```text
output/final.mp4
```

---

## 三、本地网页版运行方式

项目已经支持本地网页操作。

在项目目录运行：

```bash
python app.py
```

看到类似下面的信息后：

```text
Running on http://127.0.0.1:5000
```

打开浏览器访问：

```text
http://127.0.0.1:5000
```

注意：运行 `python app.py` 的 CMD 窗口不能关闭。关闭后，网页服务会停止，浏览器会显示“拒绝连接”。

---

## 四、替换成自己的旅游图片

把真实旅游图片放进 `images/` 文件夹，建议命名为：

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

图片建议使用 16:9 横屏。如果不是 16:9，程序会自动裁剪成 1920 x 1080。

---

## 五、添加背景音乐

把背景音乐放到：

```text
audio/bgm.mp3
```

生成视频时，程序会自动：

- 把背景音乐音量压低
- 如果音乐比视频短，自动循环
- 开头淡入
- 结尾淡出
- 和 AI 旁白混合

---

## 六、修改字幕和旁白

打开 `script.json`。

主要字段说明：

```json
{
  "title": "慢慢抵达泉州古城",
  "duration_per_image": 6,
  "aspect_ratio": "16:9",
  "subtitles": [],
  "voiceover": ""
}
```

说明：

- `title`：视频标题，目前主要作为配置记录
- `duration_per_image`：每张图片持续秒数
- `aspect_ratio`：当前固定为 `16:9`
- `subtitles`：画面底部字幕，每行对应一张图片
- `voiceover`：AI 旁白朗读文本

如果 `voiceover` 为空，程序会自动把 `subtitles` 合并成旁白。

---

## 七、推荐运行顺序

第一次测试建议按这个顺序：

```bash
pip install -r requirements.txt
python scripts/generate_demo_images.py
python main.py
```

确认 `output/final.mp4` 能正常生成后，再运行网页版：

```bash
python app.py
```

---

## 八、常见问题

### 1. 浏览器打开 127.0.0.1:5000 显示拒绝连接

原因：没有启动 Flask 服务，或者 CMD 窗口被关闭。

解决：

```bash
python app.py
```

看到：

```text
Running on http://127.0.0.1:5000
```

再打开浏览器。

### 2. 视频没声音

可能原因：

- `edge-tts` 没安装成功
- 网络无法访问 edge-tts 服务
- `script.json` 里的 `voiceover` 和 `subtitles` 都为空
- 没有放背景音乐 `audio/bgm.mp3`

解决：

```bash
pip install -r requirements.txt
python main.py
```

如果 AI 旁白生成失败，程序会继续导出无旁白视频。想要背景音乐，请放入：

```text
audio/bgm.mp3
```

### 3. 生成很慢

这是正常的。项目会对每张图片逐帧生成运镜、字幕和视频编码。图片越多、分辨率越高、电脑性能越弱，生成越慢。

### 4. 示例图不真实

`scripts/generate_demo_images.py` 只用于测试流程，不用于最终成片。最终建议使用 `IMAGE_PROMPTS.md` 中的提示词生成真实摄影风格图片。

---

## 九、泉州古城图片生成建议

仓库里的 `IMAGE_PROMPTS.md` 已经提供了泉州古城 16:9 真实摄影风格图片提示词。

使用方式：

1. 打开即梦、豆包、通义万相或可灵图片。
2. 画幅选择 `16:9`。
3. 复制 `IMAGE_PROMPTS.md` 中的提示词生成图片。
4. 下载后命名为 `01.jpg` 到 `08.jpg`。
5. 放入 `images/` 文件夹。
6. 运行：

```bash
python main.py
```

---

## 十、GitHub 更新后本地同步

如果代码已经在 GitHub 修改，本地电脑要运行：

```bash
git pull
pip install -r requirements.txt
```

然后再运行：

```bash
python main.py
```

或者：

```bash
python app.py
```
