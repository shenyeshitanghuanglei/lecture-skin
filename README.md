# lecture-skin · 给网课换声音皮肤

**v0.1.0-beta.2** · 面向 Windows 10/11 x64 + NVIDIA 用户的开源试用版（MIT 许可）。请自备有权使用的 RVC 声音模型。

在 Windows 上看网课时，用本机 RVC 模型实时转换浏览器播放的声音。延迟取决于显卡、声音模型和缓冲设置。

本项目基于 [deiteris/voice-changer](https://github.com/deiteris/voice-changer)（[w-okada/voice-changer](https://github.com/w-okada/voice-changer) 的 MIT 分支）改造而来，使用 RVC 模型做声音转换，通过 VB-CABLE 虚拟声卡把系统声音送进来、再把转换后的声音放出去。

## 功能

- **声音面板**：独立桌面窗口（pywebview + WebView2），一键开始/停止变声、切换声音、调音高和音量。
- **自动音高匹配（autoPitch）**：根据老师的音高自动推算升降调，不用手动调 `tran`。
- **跟随系统默认输出**：转换后的声音输出到 Windows 当前默认播放设备，音量键直接生效；停止变声时系统声音照常播放。
- **每个声音单独设置**：音高、index ratio 等参数随声音保存，切换时自动应用。
- **声音克隆**：默认关闭的开发者实验功能，见下文。
- **控制 API**：`/vt/api`（面板使用），以及命令行小工具 `tools/vc.py`。
- 适配 RTX 50 系（CUDA 12.8 / sm_120）。

## 运行环境

- Windows 10/11 x64，支持 CUDA 12.8 的 NVIDIA 显卡和驱动。已测试 RTX 50 系。
- [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) 用于桌面面板。大多数 Windows 11 设备已预装。
- Python 3.12，用 [uv](https://github.com/astral-sh/uv) 管理依赖（`一键安装.bat` 会自动装好）
- [VB-CABLE](https://vb-audio.com/Cable/) 虚拟声卡
- 面板前端开发需要 Node.js（仓库里已包含构建好的 `panel/dist`，只运行的话不需要）

## 下载源码

- **ZIP**：在 [GitHub 仓库](https://github.com/shenyeshitanghuanglei/lecture-skin) 点 **Code → Download ZIP**，先解压完成，再打开解压后的目录。
- **Clone**：已有 Git 时，在 PowerShell 里运行下面两条命令。
- **Fork**：在 GitHub 点 **Fork**，然后在你的 Fork 里复制 **Code → HTTPS** 地址，替换下面的克隆地址。欢迎基于本项目二次开发，许可条款见 [LICENSE](LICENSE)。

```powershell
git clone https://github.com/shenyeshitanghuanglei/lecture-skin.git
cd lecture-skin
```

## 第一次变声

1. **新手引导一键准备**。双击 `一键安装.bat`（或直接双击 `声音面板.bat`，缺少环境时会自动拉起引导），它会自动检查并安装缺失的 uv、Python 3.12 和依赖（首次依赖下载约 3 GB，需要访问 PyPI 和 PyTorch 下载站），并协助打开虚拟声卡下载页。已有 uv 可在仓库目录运行 `uv sync --locked`。
2. **装虚拟声卡**。从 [VB-CABLE 官网](https://vb-audio.com/Cable/) 下载安装，安装需要管理员权限，完成后重启 Windows。
3. **启动声音面板与图形化新手引导**。双击 `声音面板.bat`。首次启动会从 Hugging Face 和 GitHub 下载基础推理权重；进入面板后会自动弹出「新手引导」（之后可随时点左侧栏底部的「新手引导」重新打开），共 3 步：环境检查（Python / uv）、虚拟声卡、功能教学。面板也可在浏览器打开，地址为 `http://127.0.0.1:18888/panel/`。
4. **接好声音**。在 Windows 音量合成器里，把正在播放网课的浏览器输出设为 **CABLE Input**，系统默认输出保持耳机或音箱。面板的「设置」页输入选 **CABLE Output**，输出选**跟随系统默认输出**。
5. **导入并试听**。进入「声音库」导入你有权使用的 RVC 模型，点「使用」。回到主页点「开始变声」，播放一段讲话，确认输入音量有变化并能从耳机或音箱听到变声。退出后可把浏览器输出切回默认设备。

只启动本地服务可运行以下命令。

```powershell
.\start.ps1
```

### 常见问题

| 现象 | 检查 |
| --- | --- |
| 双击面板没启动 | 先确认安装步骤完成，并检查 WebView2 Runtime。查看 `logs/server-console.log` 或用 `start.ps1` 看报错。 |
| 卡在下载依赖或权重 | 确认能访问 PyPI、PyTorch 下载站、Hugging Face 和 GitHub。已配置代理时，可通过 `HTTPS_PROXY` 让安装和权重下载使用代理。无法访问 Hugging Face 时，可设置 `HF_ENDPOINT=https://hf-mirror.com` 改用镜像下载基础权重（文件仍会校验哈希）。 |
| 启动后没声音 | 先播放视频，让浏览器出现在音量合成器里。确认浏览器输出接到 **CABLE Input**，面板从 **CABLE Output** 读取，输出接耳机或音箱。 |
| 音频断续 | 在「设置」切换到「稳定」延迟档，再检查 NVIDIA 驱动、模型和设备选择。 |
| 以上都解决不了 | 在「设置」点「导出诊断信息」，生成的 zip 包含版本、显卡和内存、日志和设置。到 [GitHub Issues](https://github.com/shenyeshitanghuanglei/lecture-skin/issues/new/choose) 按模板描述问题并附上该文件。 |

### 声音模型

仓库只分发源码。请从本地任意目录导入自己训练或已获授权的 RVC 模型，支持 `.pth`、`.safetensors`、`.onnx`，可加配套的 `.index`。导入后模型保存在本机。仅导入来源可信的文件。

第三方模型推荐只链接作者的原始发布页，并以可核实的声音来源和使用条款为准。当前请自备模型。

### 声音克隆（开发者实验）

普通启动时关闭「声音克隆」入口和 `/vt/api/clone` 接口。要试用，先退出已有面板和服务，再在 PowerShell 里启动实验模式。

```powershell
$env:VT_ENABLE_CLONE = 'true'
.\声音面板.bat
```

恢复默认时，关闭实验服务，在新的 PowerShell 窗口正常启动，或把 `VT_ENABLE_CLONE` 设置为 `false` 再启动。

面板左侧「声音克隆」：

1. **添加素材**：同一个人说话的声音，总共 10–30 分钟最好（至少 3 分钟）。支持 wav / mp3 / flac / m4a 等音频，mp4 / mkv / flv 等视频（自动抽出音轨），也可以直接录音。视频默认开启「分离人声」（Demucs），去掉背景音乐。
2. **处理素材**：切成 3–4 秒的短句、统一音量、提取音高，给出可用时长报告；可以逐条试听、排除有杂音的片段。
3. **训练**：快速 / 标准 / 精细三档。训练在独立进程里跑，关掉面板也会继续；训练期间显卡被占用，不能实时变声。RTX 5070 Laptop 上每分钟素材每轮约 1.4 秒。
4. **试听入库**：每隔一段保存一个版本，并把同一句话转换出来对比；挑一个满意的版本加入声音库，目标音高会按素材自动设置。

首次训练会下载 RVC v2 底模（约 210 MB，存放在 `data/pretrained_v2/`），首次分离人声会下载 Demucs 模型（约 80 MB）。请只克隆你本人或已明确获授权的声音。

开发者也可运行 `uv run python -m trainer.run --help` 查看训练命令。

## 隐私与本地文件

默认服务只监听 `127.0.0.1`，实时变声在本机进行。依赖和基础权重下载需要联网。实验模式下，点击录音时才会请求麦克风访问，录音、导入的训练素材和试听保存在本机的 `data/clone_jobs/`。导入的声音模型放在 `vendor/voice-changer/server/model_dir/`。这些目录已加入 Git 忽略规则。

## 目录结构

| 路径 | 内容 |
| --- | --- |
| `app/desktop.py` | 声音面板桌面窗口 |
| `panel/` | 面板前端（React + Vite） |
| `trainer/` | 声音克隆：素材处理、RVC 训练、导出（独立进程运行，默认关闭） |
| `一键安装.bat` / `setup.ps1` | 一键安装：装 uv → `uv sync --locked`（Python 3.12 + 依赖）→ 检查 VB-CABLE |
| `声音面板.bat` / `start.ps1` | 打开面板 / 只启动变声服务 |
| `vendor/voice-changer/` | 改造后的 deiteris/voice-changer；新增代码主要在 `server/vt/` 和 `server/voice_changer/common/AutoPitch.py` |
| `tools/` | 命令行与调试脚本（`vc.py`、离线转换、音高扫描等） |
| `tests/` | 测试 |

## 借鉴与致谢

### 直接使用的代码

| 项目 | 用在哪里 | 许可证 |
| --- | --- | --- |
| [deiteris/voice-changer](https://github.com/deiteris/voice-changer)（`master-custom` 分支，commit `b9cd071`） | 整个变声服务的基础，位于 `vendor/voice-changer/`。上游版本和归属见 [vendor/VENDOR.md](vendor/VENDOR.md) | MIT |
| [w-okada/voice-changer](https://github.com/w-okada/voice-changer)（VCClient / MMVC） | deiteris 分支的上游原项目 | MIT |
| [RVC-Project/Retrieval-based-Voice-Conversion-WebUI](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) | RVC 推理模型结构（`infer_pack`）和 RMVPE 音高提取代码，随上游一起引入 | MIT |
| [RVC-Project/Retrieval-based-Voice-Conversion-WebUI](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) 训练部分 | `trainer/` 的训练循环、损失函数、频谱计算、切片与索引做法按其 `infer/modules/train`、`infer/lib/train` 精简移植；底模 `f0G40k` / `f0D40k` 来自 [lj1995/VoiceConversionWebUI](https://huggingface.co/lj1995/VoiceConversionWebUI)（首次使用时下载） | MIT |
| [yqzhishen/onnxcrepe](https://github.com/yqzhishen/onnxcrepe) | ONNX 版 CREPE 音高提取，随上游一起引入 | MIT |

### 依赖的模型与库

- [ContentVec](https://github.com/auspicious3000/contentvec)：语音内容特征提取。首次启动时从 [wok000/weights_gpl](https://huggingface.co/wok000/weights_gpl) 下载的 `contentvec-f.onnx` 按 GPL 条款提供，本仓库不包含该文件，使用时以其发布页条款为准
- [RMVPE](https://github.com/Dream-High/RMVPE)、[FCPE / torchfcpe](https://github.com/CNChTu/FCPE)、[torchcrepe](https://github.com/maxrmorrison/torchcrepe)：音高提取
- [PyTorch](https://pytorch.org/)、[ONNX Runtime](https://onnxruntime.ai/)、[faiss](https://github.com/facebookresearch/faiss)、[librosa](https://github.com/librosa/librosa)、[FastAPI](https://fastapi.tiangolo.com/)、[sounddevice](https://github.com/spatialaudio/python-sounddevice)
- [Demucs](https://github.com/facebookresearch/demucs)（htdemucs）：声音克隆时分离人声；[PyAV](https://github.com/PyAV-Org/PyAV)：解码音频 / 视频
- [pywebview](https://github.com/r0x0r/pywebview)：桌面窗口；[React](https://react.dev/) + [Vite](https://vitejs.dev/)：面板前端
- [VB-CABLE](https://vb-audio.com/Cable/)：虚拟声卡（需另行安装）

## 许可证

项目代码按 [MIT 许可证](LICENSE) 开源：任何人都可以 Fork、修改、再发布和用于商业用途，只需在副本中保留版权声明和许可证文本。上游及第三方代码保留原许可证和版权声明，见上方致谢和 [vendor/voice-changer/LICENSE](vendor/voice-changer/LICENSE)。

MIT 许可只覆盖代码。运行时下载的权重（例如按 GPL 条款提供的 `contentvec-f.onnx`）和你导入的声音模型适用各自的条款；基于本项目再发布时，请同样核实这些文件的授权。
