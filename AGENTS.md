> [!WARNING]
> **迁移审计未完成。** 本项目的 AI 提示词曾在 `AIREADME.md` → `AGENTS.md` 迁移中发生内容合并、删改、语义重写，或无法用 Git 证明为纯改名。当前内容可能与项目真实状态不一致。
>
> 在进行任何项目修改前，必须先核对迁移前后的 Git 历史、当前代码与配置、真实运行/部署状态，以及项目内部调用模型的提示词和启动参数。确认迁移内容与真实状态一致后，删除本警告，再继续正常修改；不得仅依据本文件恢复、删除或改变项目行为。

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the standard five-role triage label vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

This repository uses a single-context documentation layout. See `docs/agents/domain.md`.

## Local project contract

- Maintain the user's fork at `origin` (`AlexbeatsZ/wenyi`); use `upstream` for synchronization and do not open an upstream PR unless requested.
- API keys stay in environment variables. On Windows, run Python through `uv` and keep runtime logs/backups under `%LOCALAPPDATA%\Temp\.agents\`.
- The high-quality pipeline is Gemini 3.6 Flash Medium initial translation, Gemini Pro High polishing, and Codex Sol High independent review/fixing. Narrative facts must be projected by chapter/segment visibility so later revelations do not leak into earlier translation.
- Final EPUB acceptance includes `7z t` plus OPF manifest/spine resolution; a readable ZIP alone is insufficient.
- Tieba publishing is preview-only unless `--publish` is explicit. Preserve the project Chrome profile, single-instance lock, rate limits, atomic checkpoint, and server-persisted-floor verification; never bypass CAPTCHA.

## Current state

- The clean rebuild completed 137/137 translated and reviewed chapters. The maintained state is under `state/revisions/20260721_clean/<book>`; recheck the current config and state before resuming any pipeline.
- The latest recorded EPUB after terminology corrections had SHA-256 `7D23022B4AF2DC346977A2E733F225E975629B6C981F0E02E7C598924760A038` (verified 2026-07-30); regenerate and revalidate after further edits.

## Active work

- Resume Tieba topic `10905826072` from the atomic publish checkpoint. The last recorded verified progress was through `ch50-part1` on 2026-07-31; treat logs and process IDs as stale until rechecked. Keep the original `ch4-part2` floor and do not delete the later duplicate unless the user changes that decision.
