# Project Goal

- 使用 Wenyi 将长篇小说可靠地翻译为简体中文，并保留 EPUB 的目录、样式、图片和锚点。
- 当前任务：通过 SenseNova 的 OpenAI Chat Completions 兼容端点，使用 `deepseek-v4-flash` 翻译指定的日文 EPUB。
- 为正在翻译的小说提供只读局域网阅读页，允许手机边看当前译文边等待后续章节完成。

# Lessons Learned

- `llm.base_url` 必须填写 API 根地址；OpenAI 客户端会自行追加 `/chat/completions`，因此完整接口 `https://token.sensenova.cn/v1/chat/completions` 对应的配置值是 `https://token.sensenova.cn/v1`。
- API key 只通过环境变量传入，不能写入 `config.yaml` 或其他 Git 跟踪文件。
- 若要确保整条流水线只使用一个模型，必须同时覆盖 `strong`、`cheap`、`fast` 三个档位。
- 项目没有固定的 `light_novel` 配置枚举；本次按用户要求不手工覆盖风格，由 `prepare` 根据原文自动生成风格指南。
- 本地 GitHub 身份 `AlexbeatsZ` 对上游 `BigDawnGhost/wenyi` 无写权限；本次提交保留在本地 `main`，推送返回 HTTP 403。
- 翻译中的章节 JSON 会保留在 `state/<书名>/chapters`；局域网阅读器只读这些文件，不修改、不锁定，也不删除翻译状态。
- 阅读页每 60 秒读取一次全书进度；仅当当前章节的文件修订时间发生变化时重载正文，并提供手动查询按钮。
- 手机端浏览器 QA 发现窄屏下手动查询按钮会被挤压，已为按钮设置固定宽度；390×844 视口下章节切换、查询和字号切换均正常。

# Task Board

- [x] 检查项目配置、CLI 文档、源 EPUB 和 Git 状态。
- [x] 验证 SenseNova 端点、模型 ID、JSON 模式及 DeepSeek 思考参数。
- [x] 完成 `prepare`，保留项目自动生成的风格指南。
- [ ] 后台自动翻译整本 EPUB；进程正常、错误日志为空，完成后会自动组装输出，实时进度可从阅读页查看。
- [ ] 翻译完成后检查 `output` 目录中的中文 EPUB；可用 `trans-novel status` 随时查看进度，同一命令可断点续跑。
- [x] 新增 `reader` 局域网移动阅读器：暗色排版、每分钟自动查询、手动查询、章节导航、字号调节和原文对照开关。
- [x] 阅读器通过 3 项自动测试及 390×844 手机视口交互检查；不会干扰后台翻译。
- [x] 已创建本地提交；上游推送因仓库写权限不足而失败，本地提交暂未同步到 `origin/main`。
