# Ken Burns 外部运镜集成

已作为 Git 子模块接入的项目：

- `third_party/3d-ken-burns`: 单张图片 3D Ken Burns 视差运镜，需要 PyTorch/CuPy/CUDA 环境，许可证为 CC BY-NC-SA 4.0，适合非商业研究或实验。
- `third_party/kburns-slideshow`: 多图片 FFmpeg 运镜幻灯片，已接入 Web 第 5 步的视频引擎选择器。
- `third_party/Ken-Burns-Slideshow`: Windows/WPF 桌面工具，保留为参考工具。
- `third_party/kdenlive_slideshow_editor`: Kdenlive 项目编辑器，适合后续导出 `.kdenlive` 工作流。

## 使用方式

Web 页面第 5 步选择视频生成引擎：

- `项目原生电影感剪辑`: 默认流程，保留当前字幕、旁白、片头片尾和版式剪辑。
- `kburns-slideshow 运镜幻灯片`: 调用 `third_party/kburns-slideshow/kbvs-cli.py`，为 `images/` 内所有图片生成缩放、平移和淡入淡出转场。
- `3d-ken-burns 单图 3D 视差`: 调用 `third_party/3d-ken-burns/autozoom.py`。当前封装要求 `images/` 中只保留一张图片。

也可以命令行切换：

```powershell
$env:VIDEO_RENDER_ENGINE="kburns-slideshow"
python main.py
```

```powershell
$env:VIDEO_RENDER_ENGINE="3d-ken-burns"
python main.py
```

恢复默认：

```powershell
$env:VIDEO_RENDER_ENGINE="native"
python main.py
```

## 依赖说明

`kburns-slideshow` 需要系统可调用 `ffmpeg` 和 `ffprobe`。如果不在 PATH 中，可以设置：

```powershell
$env:FFMPEG="C:\path\to\ffmpeg.exe"
$env:FFPROBE="C:\path\to\ffprobe.exe"
```

`3d-ken-burns` 需要 CUDA、CuPy、PyTorch 和模型权重，首次配置成本较高。建议先用 `kburns-slideshow` 完成批量文旅图片视频，再单独为重点封面图尝试 3D 视差。

