# Project Goal

- 使用 Wenyi 将长篇小说可靠地翻译为简体中文，并保留 EPUB 的目录、样式、图片和锚点。
- 当前任务：通过 SenseNova 的 OpenAI Chat Completions 兼容端点，使用 `deepseek-v4-flash` 翻译指定的日文 EPUB。
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
- Kakuyomu 原始 EPUB 与 Wenyi state 的 source 均完整保留日文 `「」`；本书引号缺失发生在模型翻译/润色后的 target。提示词不能作为唯一防线，应按 source 逻辑段边界确定性恢复外层中文 `“”`。

# Task Board

- [x] 检查项目配置、CLI 文档、源 EPUB 和 Git 状态。
- [x] 验证 SenseNova 端点、模型 ID、JSON 模式及 DeepSeek 思考参数。
- [x] 完成 `prepare`，保留项目自动生成的风格指南。
- [ ] 后台自动翻译整本 EPUB；进程正常、错误日志为空，完成后会自动组装输出，实时进度可从阅读页查看。
- [ ] 翻译完成后检查 `output` 目录中的中文 EPUB；可用 `trans-novel status` 随时查看进度，同一命令可断点续跑。
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
