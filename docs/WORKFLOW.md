# 工作流操作手册（中文版）

> English version: [WORKFLOW_EN.md](WORKFLOW_EN.md)

本仓库提供一套经过实战检验的 **云端大脑（Claude on the Web / Claude Code Cloud）+ 本地算力双手（Codex CLI / Claude Code CLI）** 异步协同闭环。GitHub Actions Self-Hosted Runner 是两者之间唯一的加密通信与审计通道。
本地大体量数据集（如 500GB+ 医学影像、视频语料、LLM 评测集）严格保留在本地工作站（macOS / Linux / Windows），绝不进入 Git 仓库。

---

## 1. 角色分工与安全边界

| 角色 | 职责 | 硬件级限制 |
|---|---|---|
| **云端大脑（Claude Web）** | 阅读历史记录、提出假设、冻结实验协议（`PROTOCOL.md`）、编写 `.ai/task.md`、派发任务、审计代码与结果 | 不直接触碰本地大体量原始数据；未冻结 `PROTOCOL.md` 前不下实验结论 |
| **本地 Agent（`ai-loop.yml`）** | 按 `.ai/task.md` 编写代码、运行测试、读取本地只读数据集、产出 `< 5 MB` 的统计指标与报告 | 无法修改 `.github/`、`.ai/`、`.claude/`、`scripts/ci/` 及任何 `**/PROTOCOL.md`（护栏自动回滚）；不自行 `git push` |
| **远程命令（`remote-cmd.yml`）** | 直接在本地执行确定性命令（跑 `pytest`、目录普查 `survey.py`、硬件检测 `smoke_test.py`、取回指定文件） | 默认 `commit=false`（丢弃工作区改动，仅记录输出）；取回的文件只进 14 天私有 Artifact |

---

## 2. 两条执行通道

| 通道 | 适用场景 | 触发方式 | 产物位置 |
|---|---|---|---|
| `ai-loop.yml` | 需要写代码、跑实验、自主调试的完整任务 | 1) 修改 `.ai/task.md` 推送到 `main`/`ai-loop` 自动触发<br>2) `workflow_dispatch` 手动触发（可填一次性 `task` 文字，可选 `codex`/`claude`/`python` 后端） | `runs/<n>/`：`summary.md`、`metrics.json`、`meta.json`、`guard.md`、`task.md`、`agent_tail.log`；完整目录同时上传至 Artifact `run-<n>` |
| `remote-cmd.yml` | 确定性操作：跑测试、目录普查、查看 GPU 状态、取回本地文件供云端阅读 | `workflow_dispatch`：填 `command`，可选 `workdir`、`shell`（`bash`/`powershell`/`cmd`）、`collect`（分号分隔路径）、`commit`（默认 `false`） | `runs/cmd-<n>/`：`command.txt`、`output.txt`（最后 2000 行）、`collect_manifest.txt`、`meta.json`、`guard.md`；完整输出与取回文件在 Artifact `cmd-<n>` |

两条通道共用 `concurrency: local-workstation-loop`，同一时刻只运行一个任务，避免多任务并发争抢本地 GPU 或写坏工作区。

---

## 3. 五重安全护栏（`scripts/ci/guard.sh` & `guard.py`）

每次任务执行前，Workflow 会先将 `scripts/ci/` 复制到 `$RUNNER_TEMP/ci`（**预冻结机制**：即便本地 Agent 在运行中篡改了工作区里的 `scripts/ci/`，推送前执行的依然是临时目录里未受污染的护栏脚本）：

1. **统一提交收口（Soft Reset）**：若本地 Agent 自行执行了 `git commit`，护栏会先 `git reset --soft "$BASE_SHA"`，确保所有改动无遗漏地经过同一次审查。
2. **受保护路径自动回滚**：对 `.github/`、`.ai/`、`.claude/`、`scripts/ci/` 以及所有 `**/PROTOCOL.md` 的修改或新增一律自动撤销。**受保护路径只能由云端大脑直接推送修改。**
3. **大文件拦截（> 5 MB）**：单文件超过 5 MB 自动从暂存区撤下（保留在 runner 本地磁盘），防止原始数据集或模型权重误入 Git 历史。
4. **凭据与密钥拦截**：自动拦截 `.env*`、`*.pem`、`*.key`、`id_rsa*`、以及文件名包含 `secret`/`token`/`credential`/`password` 的文件，并在日志中用正则抹除 `sk-*`、`ghp_*`、`github_pat_*`、`AKIA*` 等 Token。
5. **透明审计日志（`guard.md`）**：每次运行自动生成 `runs/<n>/guard.md`，记录所有被撤销或拦截的文件，并输出到 GitHub Actions 页面摘要。

---

## 4. 常用操作速查（`remote-cmd.yml`）

| 场景 | `inputs` 参数示例 |
|---|---|
| 检查本地硬件、GPU 与数据路径 | `command`: `python3 scripts/tools/smoke_test.py` |
| 普查本地工作目录结构（深度 2） | `command`: `python3 scripts/tools/survey.py "~/projects" --depth 2` |
| 仅取回本地某个项目的代码供云端阅读 | `command`: `true`；`collect`: `~/projects/my-app/**/*.py;~/projects/my-app/README.md` |
| 在本地另一个项目目录跑测试 | `workdir`: `~/projects/my-app`；`command`: `python3 -m pytest -q` |
| Windows 查看系统计划任务或 GPU | `shell`: `powershell`；`command`: `nvidia-smi` |

---

## 5. 本地机器休眠 / 离线时的排队策略（规避 24 小时超时）

- **派发前先确认在线状态**：通过 `actions_list` 查看最近一次运行是否在几秒内被 runner 接走。若排队数分钟仍为 `queued`，说明本地电脑处于休眠或关机状态。
- **使用 `.ai/next_task.md` 暂存任务**：GitHub Actions 中排队的任务若超过 **24 小时**未被 Self-Hosted Runner 接走会被系统自动作废。因此当本地电脑离线时，**不要直接修改 `.ai/task.md`**，而是将下一轮任务写入 `.ai/next_task.md`（不会触发 Workflow），待本地电脑上线后再改名为 `.ai/task.md` 推送。
