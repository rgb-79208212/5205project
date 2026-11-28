# Evidence-Driven Deep Research

基于 **MCP、可组合 Agent Skills 与混合检索 RAG** 的研究助手：将问题组织为任务图，独立阅读分支按依赖协作，检索原文证据、综合并审查；发现缺口后定向补查，输出有引用的回答、未解决的问题和检查记录。

适合范围明确的论文问题、方法比较和证据整理。这是有时间与调用预算的研究原型，不能保证任意领域的答案正确或检索完整。

项目文档：[改进报告](report-docs/01_改进报告.md) · [项目叙述](report-docs/02_项目叙述.md) · [启动与使用说明](report-docs/03_启动与使用说明.md)。项目叙述按课程报告五部分组织，含完整运行案例与界面图。

## 启动与访问

需要 Python 3.12+。在本目录打开 PowerShell，首次运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe prepare_models.py
Copy-Item .env.example .env   # 仅首次创建，已有配置不要覆盖
```

在 `.env` 填写 `LLM_API_KEY`，默认使用 DeepSeek。建议同时填写 `OPENALEX_API_KEY`（可从 [OpenAlex 设置](https://openalex.org/settings/api) 获取），以减少主检索接口的匿名限流。公开论文、百科和本项目使用的公开数据集无需另填密钥。之后启动：

```powershell
.\.venv\Scripts\python.exe app.py
```

打开 [http://127.0.0.1:8776/research](http://127.0.0.1:8776/research)。本工作区已有环境和配置，可直接启动；`Ctrl+C` 停止。

`prepare_models.py` 首次下载公开的本地 CPU 检索模型到 `.models/`，无需额外 API key；它们用于英文论文检索。默认语义检索仅使用已准备的Embedding权重；切换到可选重排模式前，也应完成模型准备。Embedding缺失或加载失败时，会明确记录降级为 BM25，不能将降级当作语义检索成功。

- **其他电脑运行**：复制源码，重新建立环境、安装依赖并填写该电脑的 `.env`。不复制 `.venv`，不分享密钥及个人运行记录。
- **同一局域网访问**：使用 `app.py --host 0.0.0.0` 启动，其他电脑访问 `http://运行电脑的局域网IP:8776/research`；需要网络和防火墙允许该端口。
- **端口被占用**：使用 `app.py --port 8777`，浏览器相应改为端口 8777。

## 如何使用

输入问题，点击 **Research**。最好说明对象、比较维度和希望核对的内容，例如：

> In the original Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks paper, what external information does the retriever supply to the generator? Answer this one factual point from the original paper.

也可以询问方法的适用条件、不同论文的共同结论或实验局限。范围越大，越可能只能得到部分回答。没有证据时，系统应说明缺口，而不是猜出结论。

默认最多 **3 轮、36 次 MCP 调用、240 秒**。取回段落和登记引文也占调用次数。可在 `.env` 调整预算；增加预算不保证补齐证据。

复杂问题最多同时运行两个阅读分支，预算在分支与综合审查间分配。36次指研究动作预算，Host自动交接候选/证据的协议请求另记；并行可能增加模型调用，不能保证更快或更准确。

## 网页各部分

| 部分 | 用途 |
|---|---|
| 问题输入与示例 | 输入研究问题并开始运行 |
| 执行进度 | 显示规划、读取、审查、补查和整理阶段 |
| Show MCP details | 查看实际工具调用、参数、耗时与返回情况 |
| 回答与摘要 | 展示通过发布检查的结论；综合、条件推断和计算结果有不同标签 |
| 状态说明 | 分别显示结论检查、任务完成度和执行情况 |
| Coverage and withheld claims | 展开查看逐项覆盖情况、被扣留的结论及原因 |
| 引用与 Evidence | 查看原文、位置、访问级别、关联结论及文档版本哈希 |
| 表格与图表 | 表格对应结论与证据；图表展示研究覆盖记录，不表示方法性能排名 |
| Methodology / Limitations | 说明处理方式、未解决的问题及停止原因 |
| Export PDF | 使用浏览器打印当前回答 |

页面保留原有布局。四种任务结果的含义：

- `complete`：限定的子问题与验收项已满足，且有可发布结论。
- `partial`：已有通过检查的结论，但仍有未满足的要求。
- `sources_only`：取得可登记引文，但尚无可发布结论。
- `no_evidence`：没有取得可登记的实质证据，覆盖图本身不算原文证据。

部分完成不等于所有结论都不可信；正常运行结束也不等于答案一定完整。未检查、部分支持或有待解决冲突的结论保留在审查记录中，不进入正式回答。

## MCP 与 Skills 如何工作

```text
问题 → 子问题与验收项 → 搜索和原文阅读 → 精确引文与候选结论
                                               ↓
                                  程序检查 + 分开的模型审查
                                               ↓
                定向补查 ← 未满足的验收项       获准结论
                                               ↓
                            必要时多证据综合/计算，再次审查
                                               ↓
                                  回答 + 引用 + 缺口与记录
```

**MCP 提供能力边界**：文献发现与文献库分为两个实际服务，在 `mcp_servers.json` 注册。Host发现各服务的工具及参数格式，通过项目定义的能力标识路由，服务内部工具名可以不同。文献与证据通过Host登记交接，服务不并发写同一份状态。

八个研究工具的分工：

| 工具 | 作用 |
|---|---|
| `search_papers` | OpenAlex 检索、Crossref 回退；必要时使用 arXiv，返回来源 ID 与阅读链接 |
| `read_source` | 读取已登记来源的公开 PDF/HTML，区分正文、摘要、元数据或样本 |
| `search_passages` | 在已保存全文中进行BM25与本地语义检索、排名融合；可选交叉编码器重排序，保留段落ID并补充相邻上下文 |
| `get_passages` | 取回已保存版本的指定段落及必要的相邻上下文 |
| `register_evidence` | 检查引文确为保存段落的连续原文，登记位置、字符偏移和版本 |
| `search_datasets` | 可选的数据集发现入口 |
| `inspect_dataset` | 可选的有界字段与样本检查，不能代表总体统计 |
| `search_encyclopedia` | 可选的概念背景，不能替代主要实验依据 |

另有 `register_sources`、`import_branch` 两个Host交接能力，不赋予研究Skills。审查通过MCP Resources读取固定哈希版本的正文，按需选取所需上下文。分支证据合并前重新验证引文和偏移，不直接相信分支声称的证据有效性。

**Skills 规定阶段职责、输出格式和工具权限**。先读取元信息，激活时载入完整指令；输出结构和调用权限由程序检查。

保留四个角色，并将输出契约迁入各自包内：

| Skill | 职责与限制 |
|---|---|
| Planner | 定义子问题、逐项验收条件及搜索/重读动作；后续不能降低原始目标来伪造完成 |
| Reader | 提取精确引文与候选结论；需要时组合多条获准证据，新结论仍须送审 |
| Auditor | 单独模型调用，检查结论支持程度、适用条件、冲突及逐项覆盖；可取回保存段落 |
| Writer | 只组织获准结论 ID，没有工具权限，不能自由加入事实或数字 |

Planner还会按描述选择任务型方法包：`original-paper`（原始论文阅读）、`experiment-comparison`（实验条件比较）、`counterevidence`（限制与反证）、`cross-paper-synthesis`（跨论文综合）。方法包继承角色约束，可携带按需加载的参考资料；`runtime.json`是本项目的注册扩展，声明契约和能力，并非官方Skills必填格式。新增兼容研究输出协议的方法包无需修改中心Schema或硬编码其名称。

任务图中，每条阅读分支拥有独立模型上下文、MCP连接、目录和预算。依赖任务获得获准证据后才进行综合；综合有缺口时可按审查意见重试。单点问题只启动必要分支，不强制所有方法都执行。方法包不是独立训练的模型，分支隔离也不等于独立专家验证。

每轮补查记录关联的缺口。已有来源可回答时定向重读，仍缺证据时再搜索。多来源综合结论引用多条证据，并重新接受审查。

子问题可以声明依赖：例如先分别阅读论文 A、B，再让综合任务复用前两项的获准证据。依赖不能循环；综合结论不会因父结论已通过就自动获准。

本地索引使用BGE-small英文Embedding和BM25，按RRF融合排名。默认 `RETRIEVAL_MODE=hybrid-no-rerank`；可改为 `hybrid` 启用TinyBERT重排序，也可选 `lexical`、`bm25`、`dense`。小型重排模型在本次有限诊断中降低了目标段落召回，因此保留可选，并未默认启用。缓存同时绑定原文、段落文本和模型准备记录，不能跨版本混用证据。

## 验证的范围

- **引文**：固定到具体段落和版本；错误 ID 或改写的“原文”会被拒绝。
- **数值**：检查数字、已识别单位及主体/指标/条件的局部文本绑定，能拦截部分对象错配，不能覆盖所有自然语言数值关系。
- **计算**：支持明确的差值、比值、相对变化；使用获准数值，由程序生成结果并保存输入与公式。条件不一致或分母为零会被拒绝。
- **语义**：Auditor 判断原文是否真正支持结论。它使用新上下文，但可能与 Reader 使用同一模型，仍可能误判。
- **覆盖**：同时检查逐项验收条件和研究来源门槛；论文数量不等于回答完整。
- **研究去重**：按明确的 DOI、arXiv、版本关系或规范化后完全相同的标题归组；不保证作者或机构独立。
- **冲突**：核对任务、数据集、指标和实验协议；条件不同或信息不足时不能直接宣布可比实验相互矛盾。

**DOI 不是通过凭证**。没有 DOI 的可读原文可以使用；只有元数据的 DOI 记录不能支持实质性结论。摘要只能支持其明确写出的有限内容，不能满足需要全文细节的任务。

哈希用于定位文档版本，不能证明内容真实或来源权威。解析可能丢失表格、公式或部分布局；不做扫描件 OCR，不绕过登录和付费限制。限流、正文不可访问等警告和未完成项会保留。

## 测试、评估与追溯

```powershell
# 本地回归，不调用云模型
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 合成诊断与反馈流程对照，不调用云模型
.\.venv\Scripts\python.exe evaluation/run.py --feedback

# 合成例加入真实模型审查，产生调用费用
.\.venv\Scripts\python.exe evaluation/run.py --model --feedback

# 实际联网检索、阅读及模型验收，产生调用费用
.\.venv\Scripts\python.exe .planning/verify_live.py
```

[评估说明](evaluation/README.md) 解释指标、样例范围，以及如何导出真实运行供人工核对。16 个合成问题、64 条候选结论用于诊断机制，不等于真实论文的人工评测，不能据其结果宣称任意问题的准确率。

每次研究保存在 `runs/<研究编号>/`：`tool-state.json` 保存来源、原文版本和引文；`research-state.json` 保存原始任务、候选结论、审查、缺口、补查动作、工具轨迹与结果。旧运行保留原始结构，不自动改写为新版结果。

新版运行还记录模型尝试次数、各阶段耗时和格式纠正的具体字段路径，便于定位问题及比较调用代价；尝试次数不是实际计费金额。

本轮优化的计划、进度、测试日志和阶段源码快照在 [.planning/qa-upgrade/](.planning/qa-upgrade/)。快照不含 `.env`、环境和运行历史；回退前再次保存当前源码，不递归删除新增文件。具体操作见 [阶段记录](.planning/qa-upgrade/progress.md)。

本次架构升级的阶段记录与检索/流程对照在 [.planning/architecture-upgrade/](.planning/architecture-upgrade/)。恢复未完成任务可执行：

```powershell
.\.venv\Scripts\python.exe research_cli.py --resume "研究编号"
```

恢复沿用原问题，保留已完成分支，重试未完成任务；旧状态先保存到该运行的 `graph-history/`，各分支尝试留在 `branches/`。网页仍使用现有入口和布局；目前恢复入口为命令行。

[优化与验收记录](.planning/qa-upgrade/summary.md) 汇总本轮阶段结果、成功与失败案例及尚存限制。

[上一轮全面核验报告](.planning/qa-upgrade/full-verification/report.md) 保留架构升级前62项测试与真实检索记录。当前版本以 [架构升级报告](.planning/architecture-upgrade/report.md) 为准：71项回归及全新环境测试通过，包含真实任务图、方法包和本地语义检索验收。测试通过不代表所有回答正确。

已观察到的限制包括：Planner 偶尔增加问题未要求的验收项；比较回答可能重复或把非互斥机制写得像对立关系；Auditor 会误判，数值与多行样本检查也可能保守扣留正确内容。重要结论应查看引用和上下文。

主要代码：`app.py` 是网页入口；`discovery_mcp.py` 与 `data_mcp.py` 是两个MCP服务，由 `mcp_servers.json` 注册；`core/` 实现能力路由、Skill运行、检索、证据与任务调度；`skills/` 保存角色和方法包；`web/` 为网页；`tests/` 为回归；`evaluation/` 为评估工具。
