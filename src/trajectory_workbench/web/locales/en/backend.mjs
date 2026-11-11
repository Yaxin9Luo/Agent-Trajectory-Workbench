// English for text the server writes in Chinese: labels with a fixed wording (the table) and
// sentences that carry numbers or names (BACKEND_PATTERNS). Part of this text is stored in the
// index at import time, so it is translated when shown, not when written. The Python source is
// named above each group; tests/test_i18n_backend.py checks that the labels there are covered.
export default {
  // signals.py FLAG_LABELS
  "原地打转": "Stuck in a loop",
  "报错后原样重试": "Same retry after an error",
  "改完未验证": "Not verified after the change",
  "改动测试/评分文件": "Edited test or grading files",
  "用到 harness 组件": "Uses harness components",
  "基础设施报错": "Infrastructure error",
  "重复/道歉": "Repetition / apology",
  "语言混杂": "Mixed languages",
  "子代理改文件": "Subagent edited files",
  // signals.py flag details and is_check
  "最后一次改动之后没有测试、回读或截图检查": "No test, read-back or screenshot check after the last change",
  "回读改过的文件": "read back the changed file",

  // readiness.py ISSUE_LABELS and details
  "超长": "Too long",
  "结尾被截断": "Truncated ending",
  "调用没有返回": "Call with no result",
  "返回没有调用": "Result with no call",
  "参数不是 JSON": "Arguments are not JSON",
  "图片缺失": "Missing images",
  "训练 token 用到 harness 组件": "Trained tokens use harness components",
  "空回合": "Empty turns",
  "训练 token 占比过低": "Trained-token share too low",
  "最后一条不是模型的回复": "The last message is not a model reply",
  "以工具调用结束，没有最后的回复": "Ends with a tool call, with no final reply",

  // harness.py KIND_LABELS / USE_LABELS
  "工具": "Tool",
  "子代理类型": "Subagent type",
  "指令": "Instruction",
  "harness 文件": "harness file",
  "调用": "calls",
  "参数里": "in arguments",
  "提到": "mentions",
  "用到其产出": "uses its output",

  // jev.py step questions
  "发现问题": "Notices a problem",
  "忽略报错": "Ignores an error",
  "误读工具返回": "Misreads a tool result",
  "思行不一": "Reasoning and action differ",
  "宣称完成": "Claims done",
  "空转/道歉": "Filler / apology",
  "违反题目约束": "Violates a task constraint",
  "打磨": "Polish",
  "依赖 harness": "Relies on harness",

  // practice.py label groups
  "结果真实性": "Result integrity",
  "理解与规划": "Understanding and planning",
  "执行": "Execution",
  "收尾与验证": "Finishing and verification",
  "长度风格": "Length and style",
  "环境与评分": "Environment and grading",
  "正向": "Positive",
  // practice.py failure labels
  "钻评分器空子": "Games the grader",
  "改测试/评估": "Tampers with tests / evals",
  "硬编码输出": "Hard-coded output",
  "字面完成未达意图": "Letter, not spirit",
  "格式对内容错": "Right format, wrong content",
  "夸大完成度": "Overclaims completion",
  "偷看答案": "Reads the answers",
  "意识到在被测": "Aware of being tested",
  "理解错题": "Misreads the task",
  "错误前提": "False premise",
  "知识缺口": "Knowledge gap",
  "遗忘早期约束": "Forgets an early constraint",
  "没读懂工具返回": "Misreads a tool result",
  "诊断错误": "Wrong diagnosis",
  "工具参数错": "Wrong tool arguments",
  "该调没调": "Missing tool call",
  "附带破坏": "Collateral damage",
  "未验证就提交": "Submits without verifying",
  "验证太弱": "Weak verification",
  "过早停止": "Stops too early",
  "预算没管好": "Poor budget management",
  "冗余/自我确认": "Verbose / self-confirmation",
  "风格漂移": "Style drift",
  "harness 痕迹": "harness leak",
  "环境故障": "Environment failure",
  "题目歧义": "Ambiguous task",
  "评分器太松": "Grader too lenient",
  "评分器太严": "Grader too strict",
  "优秀样本": "Exemplar",
  "好的自我验证": "Good self-verification",
  "好的错误恢复": "Good error recovery",
  "如实汇报": "Honest report",
  "评分器误判": "Grader error",
  // practice.py severities, interventions, attributions, buckets
  "轻": "Low",
  "中": "Medium",
  "重": "High",
  "数据": "Data",
  "奖励/评分器": "Reward / grader",
  "环境": "Environment",
  "无需干预": "No intervention needed",
  "模型": "Model",
  "harness（指令/Hook/Skill/MCP 设计）": "harness (instruction / Hook / Skill / MCP design)",
  "环境/基础设施": "Environment / infrastructure",
  "题目": "Task",
  "评分器": "Grader",
  "不确定": "Unclear",
  "高分但可疑": "Suspicious pass",
  "失败（按类型）": "Failure (by type)",
  "同题成败对照": "Pass and fail on the same task",
  "新旧 checkpoint 同题": "Same task, two checkpoints",
  "随机对照": "Random control",
  // practice.py queue reasons
  "同一道题的成功样本": "The passing sample of the same task",
  "同一道题的失败样本": "The failing sample of the same task",
  "Jev：宣称完成前没有验证": "Jev: claimed done before verifying",
  "随机抽样：防止筛选带偏": "Random sample: guards against selection bias",

  // rewrite_review.py error types, miss types, gate reasons
  "语义丢失": "Meaning lost",
  "新编内容": "Invented content",
  "归因残留": "Leftover attribution",
  "改过头": "Overreach",
  "不通顺": "Incoherent",
  "其他": "Other",
  "文字里的归因残留": "Leftover attribution in the text",
  "工具参数里的依赖": "Dependency in tool arguments",
  "压缩摘要和相邻段落不同步": "The compaction summary and the neighbouring segment are out of sync",

  // service.py
  "整理 episode": "Grouping episodes",
  "完成": "Done",
  "（随摘要同步）": "(synced with the summary)",
  "（流水线未记录）": "(not recorded by the pipeline)",
  "（结构变化）": "(structural change)",
};

// [pattern, render(match, tb)]: sentences with numbers or names. `tb` translates a nested part.
export const BACKEND_PATTERNS = [
  // signals.py
  [/^同一调用连续 (\d+) 次$/, (m) => `Same call ${m[1]} times in a row`],
  [/^(\d+) 次报错后以相同参数重试$/, (m) => `${m[1]} retries with the same arguments after an error`],
  [/^改动了已有的 (.+)$/, (m) => `Changed existing ${m[1]}`],
  [/^重复段落 (\d+) · 道歉\/自我确认 (\d+)$/, (m) => `Repeated paragraphs ${m[1]} · apology / self-confirmation ${m[2]}`],
  [/^(\d+)\/(\d+) 段正文换成另一种语言$/, (m) => `${m[1]}/${m[2]} text segments switch to another language`],
  [/^(\d+) 处输出像环境\/服务故障$/, (m) => `${m[1]} outputs look like an environment or service failure`],
  [/^子代理有 (\d+) 次写操作$/, (m) => `The subagent made ${m[1]} writes`],
  [/^命令查看了 (.+)$/, (m) => `a command looked at ${m[1]}`],
  [/^命令 (.+)$/, (m) => `command ${m[1]}`],
  [/^工具 (\S+)$/, (m) => `tool ${m[1]}`],
  // readiness.py
  [/^约 ([\d,]+) tokens，超过 ([\d,]+)$/, (m) => `About ${m[1]} tokens, over ${m[2]}`],
  [/^(\d+) 个工具调用没有返回$/, (m) => `${m[1]} tool calls have no result`],
  [/^(\d+) 条工具返回找不到对应调用$/, (m) => `${m[1]} tool results have no matching call`],
  [/^(\d+) 个工具参数不是合法 JSON$/, (m) => `${m[1]} tool arguments are not valid JSON`],
  [/^(\d+) 步引用的图片文件不存在$/, (m) => `${m[1]} steps refer to image files that do not exist`],
  [/^训练 token 里用到 harness 组件：(.+)$/s, (m, tb) => `Trained tokens use harness components: ${tb(m[1])}`],
  [/^(\d+) 个空的模型回合$/, (m) => `${m[1]} empty model turns`],
  [/^训练 token 只占 ([\d.]+%)$/, (m) => `Trained tokens are only ${m[1]}`],
  // harness.py describe(): "工具 name（调用 3 步 · 提到 1 步）、… 等 7 个"
  [/^(.+?) 等 (\d+) 个$/s, (m, tb) => `${tb(m[1])} and others, ${m[2]} in total`],
  [/^(MCP|工具|Skill|子代理类型|Hook|指令|harness 文件) (.+?)（([^（）]*)）(?:、(.+))?$/s, (m, tb) => {
    const counts = m[3].split(" · ").map((part) => {
      const piece = /^(\S+) (\d+) 步$/.exec(part);
      return piece ? `${tb(piece[1])} ${piece[2]} steps` : part;
    });
    const head = `${tb(m[1])} ${m[2]} (${counts.join(" · ")})`;
    return m[4] ? `${head}, ${tb(m[4])}` : head;
  }],
  // practice.py
  [/^同题不同 checkpoint：(.+)$/, (m) => `Same task, different checkpoints: ${m[1]}`],
  [/^评分通过，但 (.+)$/s, (m, tb) => `Passed grading, but ${m[1].split("；").map(tb).join("; ")}`],
  [/^步数 (\S+)，成功样本 P(10|90) 为 (\S+)$/, (m) => `${m[1]} steps; P${m[2]} of passing samples is ${m[3]}`],
  [/^失败类型：(.+)（本集合 (\d+)\+ 条）$/, (m, tb) => `Failure type: ${tb(m[1])} (${m[2]}+ in this collection)`],
  // rewrite_review.py gate()
  [/^流水线状态：(.+?)(?:（(.+)）)?$/s, (m) => `Pipeline status: ${m[1]}` + (m[2] ? ` (${m[2]})` : "")],
  [/^(\d+) 处改动判错$/, (m) => `${m[1]} changes judged wrong`],
  [/^(\d+) 处漏改$/, (m) => `${m[1]} missed changes`],
  [/^(\d+) 个残留确认是漏改$/, (m) => `${m[1]} residue items confirmed as missed changes`],
  [/^(\d+) 个残留还没处理$/, (m) => `${m[1]} residue items not handled yet`],
  [/^(\d+) 处带警告（(.+)）的改动还没判定$/, (m) => `${m[1]} changes with a warning (${m[2].split("、").join(", ")}) not judged yet`],
  [/^人工标为(可进|不进)(?:：(.+))?$/s, (m) => `Marked by a person as ${m[1] === "可进" ? "include" : "exclude"}` + (m[2] ? `: ${m[2]}` : "")],
  [/^无法比较：(.+)$/s, (m) => `Cannot compare: ${m[1]}`],
];
