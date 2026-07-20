# Project Goal

- 使用 Wenyi 将长篇小说可靠地翻译为简体中文，并保留 EPUB 的目录、样式、图片和锚点。
- 当前任务：通过本机 agy CLI 断点续译指定的日文 EPUB；全部主要翻译与润色改用 Gemini 3.5 Flash (Medium)。
- 为正在翻译的小说提供只读局域网阅读页，允许手机边看当前译文边等待后续章节完成。

# Lessons Learned

- `llm.base_url` 必须填写 API 根地址；OpenAI 客户端会自行追加 `/chat/completions`，因此完整接口 `https://token.sensenova.cn/v1/chat/completions` 对应的配置值是 `https://token.sensenova.cn/v1`。
- API key 只通过环境变量传入，不能写入 `config.yaml` 或其他 Git 跟踪文件。
- 若要确保整条流水线只使用一个模型，必须同时覆盖 `strong`、`cheap`、`fast` 三个档位。
- 项目没有固定的 `light_novel` 配置枚举；本次按用户要求不手工覆盖风格，由 `prepare` 根据原文自动生成风格指南。
- 自有修改应推送到 fork：`origin` 为 `AlexbeatsZ/wenyi`，`upstream` 为官方 `BigDawnGhost/wenyi`；不要向官方仓库直接推送，也不要未经用户要求创建 PR。
- 翻译中的章节 JSON 会保留在 `state/<书名>/chapters`；局域网阅读器只读这些文件，不修改、不锁定，也不删除翻译状态。
- 阅读页每 60 秒读取一次全书进度；仅当当前章节的文件修订时间发生变化时重载正文，并提供手动查询按钮。
- 手机端浏览器 QA 发现窄屏下手动查询按钮会被挤压，已为按钮设置固定宽度；390×844 视口下章节切换、查询和字号切换均正常。
- Antigravity CLI 1.0.x 没有单次原生 system prompt 参数；参考 OpenClaw 的适配方式，应将角色内容折叠为一条普通 `--print` 提示词，并明确这不是安全隔离边界。
- 本机 agy 1.0.13 的 `--model` 要求 `Gemini 3.5 Flash (Medium)` 这类显示名；OpenClaw 风格的短 ID 需要在 provider 内映射。agy 不返回 token usage，只能明确记录字符估算值。
- agy 1.1.4 的 `models` 仍显示友好名称，但 `--model` 实际优先接受 `gemini-3.5-flash-low` 这类短 ID；provider 应短 ID 优先，仅对明确的 unknown-model 错误回退旧显示名并缓存结果。
- agy 1.1.4 偶尔会在新进程启动时短暂把有效短 ID 报为 unknown；应先重试短 ID，再回退旧显示名，否则会把暂态注册表竞态放大为整次翻译退出。
- agy 1.1.4 的 headless print 模式可能把普通翻译提示误判成写文件任务；传入 `--mode plan` 可禁止工具写入并正常返回译文，且无需放宽全局权限。
- 即使使用 `--mode plan`，agy 1.1.4 仍可能偶发请求 `write_file` 并在 headless 模式自动拒绝；provider 必须明确要求纯文本回答、识别该拒绝文本并用全新会话有限重试，绝不能用 `--dangerously-skip-permissions` 绕过。
- agy/Gemini 的内容策略拒绝会以退出码 0 的普通英文文本返回，而不是 JSON 或 CLI 错误；这与工具权限拒绝不同。第 129 章曾因完整提示中的高中生/年龄差恋爱背景与牵手段落组合触发 Google sensitive-words 过滤，批量与逐段兜底均失败。
- Windows PID 会快速复用；后台任务是否仍运行应以 `trans-novel status` 的书级锁为准，再结合进程映像名和创建时间，不能只检查上次记录的 PID。
- 正文可配置为两阶段串行：`translation_llm` 用 SenseNova DeepSeek 快速初译，主 `llm` 用 AGY Gemini 对照原文与分层上下文精修。完整上下文不等于整本正文；全书概览、本章梗概、相关术语、最近最终译文和当前原文/初译对照即可。
- Gemini 内容策略拒绝应在 AGY 内用全新会话有限重试；持续拒绝时先逐段定位，只把仍被拒绝的精修段落交给 `translation_llm`，不能让整批静默换模型。
- AGY 在 Windows 上通过命令行参数接收提示词；标题翻译即使已按 40 项/4000 字分批，若每批仍注入上千条全量术语，也会触发 `CreateProcess` 的 `WinError 206`。标题批次只能注入当前标题实际命中的术语，provider 也应把 206 报为命令行过长而非误报 CLI 缺失。
- 最终 EPUB 验收不能只确认 ZIP 可打开；还需运行 `7z t`，并核对 OPF manifest 文件均存在、spine idref 均能解析。
- Kakuyomu 原始 EPUB 与 Wenyi state 的 source 均完整保留日文 `「」`；本书引号缺失发生在模型翻译/润色后的 target。提示词不能作为唯一防线，应按 source 逻辑段边界确定性恢复引号。
- 台湾教育部横排中文引号规范使用 `「」『』`；日轻翻译可设置 `punctuation.quote_style: source` 跟随原文。后处理应同时统一整章引号样式并修复完整逻辑段边界；英文词内撇号不能机械改成 `』`。

# Task Board

- [x] 检查项目配置、CLI 文档、源 EPUB 和 Git 状态。
- [x] 验证 SenseNova 端点、模型 ID、JSON 模式及 DeepSeek 思考参数。
- [x] 完成 `prepare`，保留项目自动生成的风格指南。
- [x] 137 个正文逻辑章节、136/136 个目录标题和 137/137 个章节标题均已完成；最终流程正常退出，`trans-novel status` 显示空闲。
- [x] 已生成并验证 `output/[榊ダダ] 屈曲ラヴァー〜身を滅ぼしてしまいそうな初恋〜.zh.epub`（938,917 bytes）：`7z t` 通过，OPF manifest 138 项、spine 138 项，缺失文件与无效 idref 均为 0。
- [x] 新增 `reader` 局域网移动阅读器：暗色排版、每分钟自动查询、手动查询、章节导航、字号调节和原文对照开关。
- [x] 阅读器通过 3 项自动测试及 390×844 手机视口交互检查；不会干扰后台翻译。
- [x] 已创建 `AlexbeatsZ/wenyi` fork；3 个自有提交无冲突变基到 fork 的官方 v0.3.3 基线并推送到 `origin/main`，保留本地备份分支 `backup/pre-fork-rebase-20260720`。
- [x] 新增 `agy`/`agy-cli` provider：普通提示词传输、JSON 输出约束、短模型 ID 映射、串行调用与文档。
- [x] agy provider 离线相关测试 39 项通过；真实普通翻译和 `complete_json()` 烟测均通过。
- [x] 变基官方 v0.3.3 后完整测试 240 项通过；仅 2 项既有 Windows `/tmp/output` 路径断言失败，与本次修改无关。
- [x] agy provider 已推送到 `AlexbeatsZ/wenyi:main`；未创建 PR。
- [x] 定位《屈曲ラヴァー》引号问题：爬虫/ingest/阅读器均未丢引号；已翻译 target 存在大批外层对话引号遗漏。
- [x] 修改前完整备份 state；脚本修复与 4 条 Gemini 定点修复合计实际变更 44 章、597 个 target，其他字段和非章节状态文件未变。
- [x] 新增 source-aware 对话引号兜底、强化翻译提示词和 dry-run/apply 修复脚本；修复后边界违规与外层双引号不平衡均为 0。
- [x] 相关 16 项测试通过；完整测试 244 项通过，仅 2 项既有 Windows `/tmp/output` 路径断言失败。
- [x] 新增 `punctuation.quote_style`：默认 `source`，翻译/润色/标题提示词和确定性后处理统一沿用 `「」『』`；保留 `zh-cn` 可选项。
- [x] 对《屈曲ラヴァー》现有状态完整备份后迁移：137 个章节文件中已译 14,636 段，8,908 个 target 改为直角引号；仅 target 字段变化，已译逻辑段边界违规为 0，未译 2,093 段保持为空。
- [x] 全书正文 137 章完成两阶段翻译；最终续跑跨过原标题阶段故障点并完成 EPUB 组装，stdout/stderr 为 `%LOCALAPPDATA%\Temp\.agents\wenyi-agy-runtime\translate-20260721-020512.*.log`，stderr 为空。
- [x] 按用户要求直接用 agy 配置覆盖 SenseNova（不备份旧配置）；创建隔离运行目录并完成 fast JSON 与 strong Translator 真实烟测。
- [x] 修复 agy 1.1.4 模型短 ID 兼容，并固定 `--mode plan` 防止 headless 翻译误触写文件权限。
- [x] 正式续译首批验证：44 段全部非空，`「」『』` 逻辑边界违规为 0，润色与术语抽取事件均成功；stdout/stderr 位于 `%LOCALAPPDATA%\Temp\.agents\wenyi-agy-runtime\translate-20260720-235826.*.log`。
- [x] Pro High 速度不符合用户要求；在 ch126 已译 256 段处安全终止旧进程树，改由 Gemini 3.5 Flash (Medium) 接管 strong/cheap 档后断点续跑。
- [x] `trans-novel status` 新增实时 `翻译中 / 处理中 / 空闲` 显示，依据书级运行锁非阻塞检测，不受遗留 `.run.lock` 文件影响。
- [x] agy unknown-model 兼容改为短 ID 有限重试后才回退旧显示名，并补回归测试。
- [x] 核对上游 v0.3.3：`upstream/main` 与 `upstream/dev` 最新提交均已包含在 fork `main`；EPUB 拆章/源布局重建与自定义功能联合测试 109 项通过，无待合并提交。
- [x] 修复 agy plan/headless 偶发误请求 `write_file` 导致第 128 章中止：禁止工具提示词并对自动拒绝启用 3 次干净会话重试。
- [x] 新增 `translation_llm`：SenseNova `deepseek-v4-flash` 负责正文初译，AGY Gemini Flash Medium 对照原文、初译和分层上下文串行精修；密钥继续读取 `SENSENOVA_API_KEY`。
- [x] 新增 Gemini 内容策略拒绝的 3 次干净会话重试与逐段 DeepSeek 回退；真实验证第 129 章故障段可成功完成两阶段处理。
- [x] 修复标题翻译 `WinError 206`：每批只注入标题命中的术语，并纠正 AGY 对 Windows 命令行过长的错误提示。
- [x] `trans-novel status` 将运行状态移动到章节表格和术语统计之后，便于直接查看最后一行。
