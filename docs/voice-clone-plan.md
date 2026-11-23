# 声音克隆（P3）规划

> 状态：M1–M5 已在分支 `feature/voice-clone` 实现，2026-11-22。已定：**在面板内训练 RVC 模型**；素材来源三种都支持（干净音频文件、视频/直播片段、面板内录音）。
> 当前试用版默认关闭克隆面板及 API，启用方式见 [README](../README.md#声音克隆开发者实验)。
>
> 和规划的出入：
> - 训练进程经 `trainer/launch.py` 脱离服务进程树启动，因为桌面面板关窗时会用 `taskkill /T` 结束服务。
> - 混合精度用 bf16（Blackwell 支持），不用 fp16 + GradScaler。
> - 共享的推理模型代码末尾有 `torch.tanh(x, out=x)`，训练进程里给该模块换了一个不带 `out=` 的 tanh，不改推理代码。
> - 试听句子默认用 Windows 自带的中文朗读（慧慧）生成，可以换成用户上传的一段录音。
> - 实测（RTX 5070 Laptop，3.6 分钟素材，batch 8）：约 5 秒/轮；Demucs 分离 1 分钟音频约 12 秒。

## 目标

用户在面板「声音克隆」页提供某个声音的素材 → 一键训练 → 得到 RVC 模型，自动加入「声音库」，直接用于现有实时变声链路。不引入新的推理引擎。

## 现状与缺口

| 已有 | 缺 |
| --- | --- |
| RVC 推理（`infer_pack/models.py` 里已经有生成器 `SynthesizerTrnMs768NSFsid` 和判别器 `MultiPeriodDiscriminatorV2`） | 训练循环、数据加载、损失函数、mel 计算（从 RVC-WebUI 引入，MIT） |
| ContentVec（ONNX）、RMVPE 权重在 `server/pretrain/` | RVC v2 预训练底模 `f0G40k.pth` / `f0D40k.pth`（lj1995/VoiceConversionWebUI，MIT） |
| `/vt/api/voices/import` 导入 `.pth` + `.index` | 训练作业管理 API、进度上报 |
| 面板导航里已有占位「声音克隆（即将）」 | 克隆页 UI、录音 |
| — | 解码视频音频（PyAV）、人声分离、切片 |

训练提取特征时**用的 ContentVec / RMVPE 和实时推理用的是同一套**，这样训练和推理拿到的特征天然一致。

## 整体结构

```
面板 Clone 页 ──HTTP──> server/vt/clone_api.py （作业管理，单作业队列）
                              │ 启动子进程
                              ▼
                     trainer/ （新顶层目录，独立进程）
                     preprocess → extract → train → index → export
                              │ 写 jobs/<id>/progress.json + train.log
                              ▼
                     完成后调用现有导入逻辑 → 新 slot 进声音库
```

- **训练放在独立子进程**里跑（`uv run python -m trainer.run --job <dir>`），不在变声服务进程内：崩溃、OOM 都不会拖死服务；关掉面板训练也继续跑。
- 作业目录 `data/clone_jobs/<id>/`（加进 `.gitignore`）：`raw/` 原始素材、`clips/` 切片、`feats/` 特征、`ckpt/` 检查点、`preview/` 试听、`progress.json`。
- **显存**：RTX 5070 Laptop 只有 8GB。训练开始前自动停止实时变声（现有的停止逻辑会让系统声音照常播放），训练结束后提示可以恢复。

## 流程细节

1. **添加素材**
   - 音频文件：wav / mp3 / flac / m4a，可多选、拖拽。
   - 视频文件：mp4 / mkv / flv 等，用 PyAV 抽音轨（PyAV 的 wheel 自带 ffmpeg，不用另装）。
   - 面板录音：WebView2 里用 `MediaRecorder` 录麦克风，传 webm 上去。需要验证 pywebview 能不能放行麦克风权限。
   - 勾选「我有权使用这个声音」才能开始训练（和 README 里的模型使用立场保持一致）。
2. **预处理**
   - 统一重采样到 40k 单声道、响度归一化。
   - 人声分离：视频素材默认开，干净音频默认关。候选方案：Demucs htdemucs（MIT）或 audio-separator + UVR MDX ONNX 模型；**要先确认它们和 torch 2.7 / cu128 兼容**（见风险）。
   - 用 RVC 的 slicer2 按静音切成 3–10 秒的片段，丢掉太短、削波、太安静的片段。
   - 生成素材报告：有效时长、片段数、平均音高。建议 10–30 分钟，**少于 3 分钟不让开始训练**，5 分钟以下给出警告。在 UI 里可以试听、排除单个片段。
3. **特征提取**：ContentVec（768 维，v2）+ RMVPE 音高，复用 `server/pretrain/` 里的权重。
4. **训练**：RVC v2、40k、带音高的模型，从底模微调。
   - 预设：快速（约 100 epoch）／标准（约 200 epoch）／精细（约 400 epoch）。实际 epoch 数按素材时长调整。
   - batch 大小按空闲显存自动选（8GB 卡大约 4–8），混合精度用 bf16，不用 DDP（Windows 下单卡训练）。
   - 每 N 个 epoch 保存一次检查点，同时用一段固定测试音频（比如一段讲课录音）转一遍，生成试听文件，方便听出过拟合、挑出最好的 epoch。
   - 支持取消；中断后可以从最近的检查点续训。
5. **索引**：用 faiss 在训练特征上建 IVF 索引，生成 `added_*.index`。
6. **导出入库**：把选中的检查点导出成推理用的小 `.pth`，复用 `import_voice` 的逻辑放进新 slot；用训练集的中位音高自动设置 `targetPitchHz`（配合 autoPitch），`indexRatio` 默认设成 0.5 左右。

## API（`/vt/api/clone/...`）

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/clone/jobs` | 新建作业（名字） |
| POST | `/clone/jobs/{id}/files` | 上传素材（multipart，可多次） |
| POST | `/clone/jobs/{id}/prepare` | 预处理（分离、切片、生成报告） |
| GET | `/clone/jobs/{id}` | 状态：阶段、进度、ETA、loss、片段列表、试听列表 |
| PATCH | `/clone/jobs/{id}/clips` | 排除／恢复某些片段 |
| POST | `/clone/jobs/{id}/train` | 开始训练（预设） |
| POST | `/clone/jobs/{id}/cancel` | 取消 |
| POST | `/clone/jobs/{id}/finalize` | 选定检查点 → 导出并入库 |
| GET | `/clone/jobs` / DELETE `/clone/jobs/{id}` | 列出、删除作业 |

面板每 1–2 秒轮询 `GET /clone/jobs/{id}`，和现有的 status 轮询方式一样。

## 面板 UI（向导形式）

① **素材**：拖拽区 + 录音按钮 + 已添加的文件列表 → ② **检查**：时长条（绿／黄／红）、片段列表可试听和排除、人声分离开关 → ③ **训练**：选预设、进度条和 ETA、loss 小曲线、每个检查点的试听播放器 → ④ **完成**：选检查点、填名字和头像 → 「加入声音库」。

## 里程碑（每个都先找你确认再往下做）

| # | 内容 | 验收 |
| --- | --- | --- |
| M1 | **训练引擎命令行版**：引入 RVC-WebUI 的训练代码到 `trainer/`，适配 torch 2.7 / cu128 / Windows，下载底模 | 用你自己的一段录音跑完整训练，产出的模型能在现有面板里导入并实时变声 |
| M2 | **预处理**：PyAV 解码、切片、人声分离、素材报告 | 一段带背景音乐的视频 → 干净片段 + 报告 |
| M3 | **作业 API**：子进程管理、进度、取消、续训、入库 | `tests/` 里用一个很短的训练配置跑通 API 冒烟测试 |
| M4 | **面板 UI**：向导四步 + 录音 | 在面板里从零做出一个声音 |
| M5 | **打磨**：检查点试听、自动设置 targetPitchHz、预设调参、README 更新 | 你试听后认可效果 |

M1 风险最大，先做。

## 风险 / 待确认

- **老训练代码和新 torch 的兼容**：`torch.stft` 的 `return_complex`、autocast / GradScaler 的新 API、Windows 下 DataLoader 多进程、Blackwell 上 bf16 的表现。
- **人声分离依赖**：demucs 的旧版本可能卡住 torchaudio 的版本；如果冲突就改用 ONNX 版 UVR 模型（要逐个核对模型许可证）。
- **显存**：训练和实时变声不能同时跑；预处理时人声分离也要按块处理长音频。
- **训练耗时**：估计 10 分钟素材、200 epoch 在 5070 Laptop 上约 30–60 分钟，M1 实测后修正。
- **WebView2 麦克风权限**：pywebview 可能需要额外配置；实在不行就退回「在浏览器打开面板录音」。
- **版权／许可**：只训练有权使用的声音；底模和引入的代码都是 MIT，在 README 致谢表里补充。
