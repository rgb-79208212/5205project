# 评估方法

这里把“程序有没有按规则执行”和“真实研究结论是否准确”分开记录。

## 架构升级的检索对照

准备本地模型后运行：

```powershell
.\.venv\Scripts\python.exe prepare_models.py
.\.venv\Scripts\python.exe evaluation/architecture.py
```

对同一组六篇公开原文、12个手选问题，比较旧关键词、BM25、语义检索、融合、融合后重排五种配置。首次缺少本地语料时，按 `retrieval_sources.json` 下载公开PDF并检查固定哈希，之后复用保存文本；文档版本变化时停止，不能继续套用旧段落标签。

输出保存于 `.planning/architecture-upgrade/retrieval-ablation.json`。这里的命中是预先列出的有限目标段落，不是穷尽相关性标签，也不是回答准确率；模型加载和缓存会影响耗时，不应将各模式的单次耗时直接当稳定速度比较。真实流程消融及其限制见项目README链接的架构升级报告。

## 可直接运行

在项目根目录：

```powershell
# 合成样例的规则检查，不使用云模型
.\.venv\Scripts\python.exe evaluation/run.py --feedback

# 同一组合成样例加上真实模型审查，会产生调用费用
.\.venv\Scripts\python.exe evaluation/run.py --model --feedback
```

`cases.py` 包含 16 个合成问题、64 条候选结论，覆盖数字错配、因果夸大、否定、摘要、样本外推、无 DOI、元数据、不同实验条件、冲突和结论边界。标签是编写样例时设定的预期，**不是独立人工标注的真实论文测试集**。

同一批候选结论比较三种检查配置：数字词元检查、增加主体关系检查、再增加模型语义审查。这是候选结论层面的对照，不是完整检索生成系统的排名。另有四类模拟场景比较关闭/开启反馈后的覆盖、调用次数和耗时；模拟耗时不能代表真实服务速度。

结果保存在 `.planning/qa-upgrade/evaluations/`，包括每条判定、理由、模型调用错误和汇总指标。模型错误按未获准处理，不隐藏在成功案例里。

## 指标的含义

- `approved_support_rate`：发布的候选结论中，符合预期支持标签的比例。
- `false_release_rate`：预期不支持的候选结论中，被放行的比例。
- `false_withhold_rate`：预期支持的候选结论中，被扣留的比例。
- 模拟 `criterion_coverage`：原始验收项中已满足的比例；它依赖模拟审查判定。

每个比例同时保存分子、分母所需的计数；没有可计算分母时为 `null`，不填成满分。

## 真实研究的人工评估

```powershell
# 替换为实际研究编号，不覆盖已存在的标注表
.\.venv\Scripts\python.exe evaluation/run.py --export-run runs/live-研究编号

# 完成人工核对后统计，路径替换为实际 CSV
.\.venv\Scripts\python.exe evaluation/run.py --score-review .planning/qa-upgrade/evaluations/live-研究编号-human-review.csv
```

CSV 列出结论、原文、位置、系统状态和是否发布。请核对原文及上下文，在 `human_support` 填 `supported` 或 `unsupported`；无法确定可填 `partial`、`uncertain` 或留空，它们会单独计为待定而不强行归类。`human_coverage`、`reviewer`、`notes` 用于记录问题覆盖、评审人及理由。

建议逐步收集不同难度的真实问题，保留失败、预算耗尽和证据不足的案例；由人工核对来源与结论，分歧经复核后再统计。覆盖率需要对照原始问题人工逐项核对，不能用候选结论通过率代替。尚未完成的人工标注不报告准确率。合成例上的满分不能推广到真实研究或任意领域。
