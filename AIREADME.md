# Project Goal

- 使用 Wenyi 将长篇小说可靠地翻译为简体中文，并保留 EPUB 的目录、样式、图片和锚点。
- 当前任务：通过 SenseNova 的 OpenAI Chat Completions 兼容端点，使用 `deepseek-v4-flash` 翻译指定的日文 EPUB。

# Lessons Learned

- `llm.base_url` 必须填写 API 根地址；OpenAI 客户端会自行追加 `/chat/completions`，因此完整接口 `https://token.sensenova.cn/v1/chat/completions` 对应的配置值是 `https://token.sensenova.cn/v1`。
- API key 只通过环境变量传入，不能写入 `config.yaml` 或其他 Git 跟踪文件。
- 若要确保整条流水线只使用一个模型，必须同时覆盖 `strong`、`cheap`、`fast` 三个档位。
- 项目没有固定的 `light_novel` 配置枚举；本次按用户要求不手工覆盖风格，由 `prepare` 根据原文自动生成风格指南。
- 本地 GitHub 身份 `AlexbeatsZ` 对上游 `BigDawnGhost/wenyi` 无写权限；本次提交保留在本地 `main`，推送返回 HTTP 403。

# Task Board

- [x] 检查项目配置、CLI 文档、源 EPUB 和 Git 状态。
- [x] 验证 SenseNova 端点、模型 ID、JSON 模式及 DeepSeek 思考参数。
- [x] 完成 `prepare`，保留项目自动生成的风格指南。
- [ ] 后台自动翻译整本 EPUB；2026-07-19 04:18 已完成 3/137 章，进程正常、错误日志为空，完成后会自动组装输出。
- [ ] 翻译完成后检查 `output` 目录中的中文 EPUB；可用 `trans-novel status` 随时查看进度，同一命令可断点续跑。
- [x] 已创建本地提交；上游推送因仓库写权限不足而失败，当前 `main` 比 `origin/main` 超前 1 个提交。
