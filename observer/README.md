# Wenyi Observer

一个与 Wenyi 主流程完全解耦的只读实时观察器。它只读取现有
`manifest.json`、`events.jsonl` 和 `usage.json`，不会导入或修改
`trans_novel`，也不会占用书级运行锁。

同一页面包含“运行监控”和“阅读情节”两个视图。阅读视图复用原 Wenyi
移动阅读器的数据口径，支持章节导航、手动刷新、字号调整、原文对照和
翻译/终审状态标记。

## 启动

在 Wenyi 项目根目录运行：

```powershell
uv run python observer\app.py "state\书名" --config config.yaml --port 8765
```

`config.yaml` 只用于显示各阶段的 provider/model，可以省略。页面默认自动
打开；不想自动打开时追加 `--no-open`。例如读取另一个项目中的书籍状态：

```powershell
uv run python observer\app.py `
  "C:\Users\Meta\Project\Workspaces\Satisfaction\work\wenyi-state\satisfaction_あなたと私の絆_游戏脚本" `
  --config "C:\Users\Meta\Project\Workspaces\Satisfaction\config.wenyi.yaml" `
  --port 8765
```

默认只监听 `127.0.0.1:8765`，浏览器每两秒读取一次快照。若端口被占用，
可追加 `--port 8766`。

## 可见性边界

- 可显示：当前阶段/章节、已落盘原文与译文、结构化审校理由、修复前后、
  回退/失败事件、调用数和 token。
- 不能显示：模型没有通过接口返回的私有思维链。观察器会明确说明数据粒度，
  不会用推测文字冒充思维过程。

## 测试

```powershell
uv run pytest -q observer\test_app.py
```
