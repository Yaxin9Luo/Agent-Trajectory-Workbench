# 长程轨迹分析平台 — 推进计划

定位（2026-09-27 用户明确）：首先是一个**长程 agent 轨迹的可视化 + 分析 + 对比平台**（Jev 辅助语义分析），
其次才是每日轨迹阅读练习。harness 视角：基础 coding agent + MCP / Skills / Instructions / Hooks。

之前的 Phase 1–3（bug 修复、错误聚合、artifact diff、跨 run 对比）已完成，见 git 历史。

## A. 通用数据层
- [x] A1 NormalizedRun 泛化：task / outcome / meta / group，MoH 专属字段可选
- [x] A2 SQLite 索引（sources、trajectories、reviews、jev_results），从 registry.json 迁移一次
- [x] A3 Adapter 协议：detect / iter_runs / load(locator) + 目录递归发现（不重复导入嵌套副本）
- [x] A4 Claude Code 适配器（会话 + stream-json；hook/instruction 附件、harness 事件归 system）
- [x] A5 Codex 适配器（rollout；注入上下文、AGENTS.md、子代理消息）
- [x] A6 OpenAI-chat / SFT 适配器（字节偏移；context/subagent 分组；manifest 关联 results.json 并校验 task id）
- [x] A7 TraceLab trial 适配器（评分、时长、token）
- [x] A8 ATIF 适配器（按 harbor 0.23 模型核对字段；Harbor trial result.json 奖励）
- [x] A9 MoH 适配器接入新协议
- [x] A10 后台导入任务 + CLI 进度

## B. 自动信号
- [x] B1 结果归一：pass/partial/fail/error/unknown + 基础设施故障提示
- [x] B2 规则信号：原地打转、原样重试、改完未验证、改已有测试（按是否有评分定严重度）、harness 痕迹、基础设施报错、冗余、语言混杂、子代理写文件、harness 层归属

## J. Jev（TypeSafe）
- [x] J1 逐步扫描（阶段 + 7 个 Noul，按证据可用性提问；截断/图片时不判“误读”）——真实轨迹抽查后两轮修正
- [x] J2 任务级：最终汇报声称 vs 评分（overclaim/underclaim）、题目歧义
- [x] J3 阅读记录标签建议（更严格措辞 + 0.6 阈值，手写样例验证）
- [x] J4 语义定位步骤
- [x] J5 结果缓存（按版本 + 源指纹）、批量分析任务、打开轨迹自动分析

## C. 阅读与记录
- [x] C1 Review 存储与 API
- [x] C2 每日练习队列（成对样本优先，再可疑成功/失败类型/随机）
- [x] C3 统计（评分器×人工、虚高率、盲判准确率、信号在成功/失败中的比例、转折点位置）
- [x] C4 Review 导出 JSONL

## D. 前端
- [x] D1 轨迹库（筛选 + 勾选对比）
- [x] D2 阅读器（步骤条、编号步骤、信号高亮、转折点、harness 层标签）
- [x] D3 右侧“分析 / 阅读记录”两个标签页
- [x] D4 练习队列（盲判时隐藏抽样原因）
- [x] D5 统计页
- [x] D6 对比视图（摘要表、工具用量差、第一次分叉）
- [x] D7 MoH 专属面板按需显示
- [x] D8 角色/阶段配色区分度

## E. 验证与文档
- [x] E1 单元测试（适配器、信号、Jev 假客户端、练习、服务、服务器、前端安全）— 183 Python + 16 Node（2026-11-10）
- [x] E2 真实样本冒烟（MoH、TraceLab、SFT、Claude Code 项目、Codex 一天的会话）
- [x] E3 浏览器实测（桌面 + 826px 窄屏）
- [x] E4 README 更新（含 episode、训练数据、搜索、工具与报错、改写对比、部署）

## F. Spec 定稿（2026-09-27 与用户 interview）
决策：做 A、C、B 三条线（D 奖励/评分器 暂缓）；标签按分组扩充 + 严重度/决定性；只用 Jev（不接推理模型）；
部署在开发机（~/.local 装 uv + Python 3.11）；库默认按 episode 一行可展开；readiness 截断长度 256K；
顺序：Bugs → A → C → B。

### F0 Bugs（先逐条复现再修）
- [x] TraceLab/SFT/Codex 工具报错识别（Exit code、<tool_use_error>、Process exited、MCP isError；无报错标记时才推断并标“推断”；单独的 Traceback 不算——输出里常见）
- [x] 失败原因取 extra_info.exception 的 classification；reward_valid（崩溃/未评分不算 0 分）；Harbor 同理
- [x] episode 计数：stats/queue 按 episode；no_verify 只看最后一段；Claude subagents/ 挂到父会话；Codex parent_thread_id 关联（含多层）
- [x] 重导入保留 jev_summary（源未变时）
- [x] SFT 里 JSON 字符串形式的 base64 图片能显示（样例 10 条共 123 张）
- [x] /files/ 加 CSP sandbox；POST 只收 application/json（防 artifact 页面发 no-cors 请求）
- [x] SFT hook 提醒识别为 hook 层；harness 痕迹也扫工具参数（训练 token）

### F0 review 修复（subagent 审查，10 条确认问题）
- [x] 无 body 的 POST 绕过 JSON 检查 → 所有 POST 必须 application/json
- [x] episode key 碰撞（Harbor 常量 session_id、同名 sample 跨 trial/导出）→ 按来源加前缀
- [x] 打开大 episode 要重解析所有成员（47s）→ 导入时记下可派出子代理的调用，树直接从索引建
- [x] 来源换集合后旧集合没重建；episode 视图的 flag 过滤漏子代理 flag；库里 episode 行看不到成员的阅读记录；侧栏“已读”单位不一致
- [x] decode_content 把 `[{"type":"PushEvent"}]` 这类工具输出清空；Harbor exception_type 为空时崩溃；旧库迁移后不重建 episode
- [x] 工具参数里的 harness 名称扫描在 MoH 开发会话里全是噪声 → 只扫运行目录
- [x] 顺带：TraceLab 崩溃+0 分不算分；MoH 全部 classification 入表；未知异常用短名做 reason；MCP isError 只认结果对象；rebuild 全程持锁；导入中途只有主轨迹算 episode

### A/C review 修复（第二个 subagent 审查，8 条确认问题）
- [x] 重导入失败会清空/重复全文索引 → 按轨迹原子替换 + 导入后清孤儿；半行坏 UTF-8 不再让整个会话导入失败
- [x] “全部集合 vs B” 只显示 B；Jev 台账失败被当成功缓存；JSON 示例被误当 harness 包装题面
- [x] 旧库导入的集合在“工具与报错”里为空 → 提示 + 一键重新索引集合
- [x] 搜索要解析所有命中轨迹（并挤掉阅读器缓存）→ 只给前 20 条轨迹生成摘要、不进缓存；统计/工具页只读需要的列
- [x] ?step 深链在有系统步骤时落空；k 不会加载更早的窗口；步骤条重绘丢 in-view 标记
- [x] 顺带：SQLite < 3.43 时全文搜索降级而不是启动失败；内联图片按调用位置寻址；异步委派不再按描述文字匹配；Markdown 片段转义 HTML/标题

### A 长程结构
- [x] A1 Episode 树：段 + 压缩边界 + 子代理挂在调用处，缺失段占位；库默认 episode 行（可展开成员）
- [x] A2 上下文/时钟轨：每步上下文 token（Claude usage / Codex token_count / ATIF metrics 实测，其余按字数估算并用最近实测校准）、压缩边界、每步耗时热度；SFT 用 ccr_requests 得到时钟
- [x] A3 打磨尾巴：改文件步骤的原因（Jev Choice：必需/修复/打磨/辅助/不明；Noul 版误报太多已弃用）、里程碑、尾巴步数与时间占比；中间分段不计
- [x] A4 需求台账（题面拆句 + Jev 筛要求；全程步骤摘要里找处理步骤；与 harness 规则冲突；每次压缩摘要是否保留）；JSON 包装的 harness 题面自动拆开
- [x] A5 委派台账（brief→汇报→父代理是否据此行动；异步汇报按 agentId 找）、计划/todo 台账（TodoWrite/TaskCreate/update_plan）、压缩审计、hook 提醒响应 —— 阅读器“台账”标签页

### C 分析工具
- [x] C1 步骤深链 ?step=（窗口式加载，任意步直接打开，地址栏跟随）、键盘导航（j/k J/K n/p e/E t c o / [ ]）、紧凑一行一步模式
- [x] C2 全集合 FTS5 搜索（contentless 索引 ~0.3KB/步、导入 +6%；中文按字短语；跳到步骤并高亮；`--no-text-index` 可关）
- [x] C3 错误模板（取真正报错的那一行，路径/数字/字符串抽象；MCP 结果取文本）+ 工具调用浏览器（A/B 两个集合并排：次/条、使用率、报错率、模板出现条数、例子直达步骤）
- [x] C4 步骤级批注（每步“批注”按钮 / 快捷键 a，可带标签，步骤条上有标记）+ 片段导出 Markdown（默认=批注步骤+转折点，或指定范围；可不写评分）；JSON 包装题面的标题改用用户请求
- [x] C5 minimap、长输出智能折叠/同命令 diff、按需加载图片

### B 训练数据
- [x] B1 训练就绪检查（256K、训练 token 占比、图片缺失、调用/返回配对、参数 JSON、截断结尾——中间分段不算、空回合）+ 阅读器“训练视角”（训练 token 高亮、上下文变淡、每步 token）+ 统计页就绪卡 + 库筛选
- [x] B2 harness 残留按位置（训练 token vs 上下文）
- [x] B3 筛选导出（raw 字节拷贝 / chat 转换；episode 视图整段导出）+ data card（json + md，含 sha256、构成、就绪、token 分位、跳过原因）；浏览器只能写到导出根目录下
- [x] B4 改写前后 diff（两个集合按样本 id 配对：步数/训练 token/harness 痕迹变化，逐条消息逐字段行 diff）；B5 修正续写 → SFT/DPO 样本（转折点 + 修正；修正可写纯文本或含 tool_calls 的 JSON；统计页导出）

### T 标签扩充（分组）
- [x] overclaim、ground_truth_access、false_premise、knowledge_gap、premature_stop、weak_verification、budget_mismanagement、misdiagnosis、collateral_damage、eval_awareness、grader_error 拆宽松/严格 + 正向 honest_report；每个标签可记严重度（轻/中/重）与是否决定性；归因加 harness；统计页显示决定性/重的计数；旧 grader_error 标签仍能显示

### 部署
- [x] 开发机：~/.local 装 uv + Python 3.11（公共源下载不通时用镜像源的 conda 建环境）；原定端口被占用 → 8416；`HOST=0.0.0.0 PORT=8416 ./deploy.sh` 启停；从本机浏览器可访问；测试在开发机上通过

## Harness 依赖（语义）
- [x] 按名字匹配（MoH / IntentInspector / 运行目录）改成按组件：相对原生 Claude Code / Codex / pi 找出 harness 加的东西（MCP、额外工具、Skill、Hook、系统提示里原生没有的一级段落、注入的 CLAUDE.md/AGENTS.md、JSON 包装规则、这些指令里点名而题面没有的文件）
- [x] 规则层：模型回合（训练 token）里调用 / 参数带 harness 文件 / 字面提到组件，按组件和步号记录；替换 harness_ref 旗标与就绪里的残留（只看训练 token）
- [x] 语义层：Jev 逐步 Choice（不依赖 / 要用或解读组件 / 拿 harness 指令当理由），组件清单放在状态里；40 步人工标注：16/16 依赖步全部命中、0 误报（Noul 版漏 4 个，弃用）
- [x] 阅读器“Harness 增加的组件”面板、库里 Jev 信号筛选（episode 任一成员）、统计页按组件的依赖条数
- [x] 评审修复：Codex 原生工具补全（exec、collaboration__*、clock__sleep 等）且 Codex 自带的 developer 消息不算指令（300 条真实 Codex 会话：误报 246 → 4，剩下 4 条确实用了 MCP 插件）；MoH 的系统提示在 model_prompt 里也读；目录（`tests/`）不算 harness 文件；改写对比按原始轨迹的组件清单判断改写后的样本；MCP 工具的短名不重复算成工具；参数先做子串预筛（83 → 5 ms/条），绝对路径里的 harness 文件也能认出
- [ ] 开发机上重新索引 Slides 并跑 Jev（开发机当前连不上）

## 改写审阅（通用，2026-09-30 spec：docs/superpowers/specs/2026-09-30-rewrite-review-design.md）
原则：平台算 diff、对齐、harness 增减、残留、分流；流水线的理由/方案条目等是可选注解，经适配器读入。
- [x] R1 `STOCK_SECTIONS` 补 "Text output (does not apply to tool calls)"；chat 适配器记下工具声明（名字 → 描述、定义哈希）
- [x] R2 词级 diff（中英混排按字/词切分，合并碎片）
- [x] R3 步骤对齐：有 index_map 时直接映射；没有时按调用 id 锚点 + 角色/文本相似度，识别合并、删除、新增；工具调用按 id 比较
- [x] R4 harness 增减（四层清单两边比较、系统提示按段、工具声明）+ 残留（原始有、改写后没有的组件在改写后训练步骤里的用法）
- [x] R5 注解适配器：中性 JSONL + 改写运行目录（results/status/rewritten tag/条款表 → 方案条目）；注解挂到 diff 上，没记录的改动单独标出
- [x] R6 存储：批次、注解、判定；分流规则；摘要同步检查
- [x] R7 接口 + CLI（建批次、导入改写结果、读记录、存判定）
- [x] R8 前端：批次列表 + 单条记录页（迷你地图、harness 卡、折叠、事件条、详情栏、判定、漏改标注、训练视角、并排、键盘）
- [x] R9 测试：合成样例单测；真实 pilot 运行上验证无映射推断 = index_map、改动数 = report.json
- [x] R10 部署到开发机（8416），建了 pilot_skills_v2 / pilot_v3_1 两个批次（原始 = Slides 2k 改写源 0924，pilot 33 条原文与之逐字一致）；浏览器走查：键盘判定、错误类型、自动跳下一处、分流联动、漏改标注、残留确认/忽略、训练视角、并排、J/K、820px 窄屏
- [x] R11 批次总览（漏斗、按类别/警告/方案条目/种类的数量和错误率、判错类型、按警告设置分流并显示影响条数、记录表按分流筛选、导出可进原行 + 重跑 chain 清单 + 判定 + manifest）、改动队列（跨记录按风险排序、筛选、键盘判定自动下一处、Enter 打开记录并定位）；记录摘要按源指纹缓存，分流按请求现算
- [x] R12 删掉旧的"改写对比"页（#/rewrite、/api/rewrites、/api/rewrite-diff、rewrite.diff_runs、rewrite_pairs）
- 真实数据验证（2026-09-30，pilot 33 条）：无映射推断的对齐 = 流水线 index_map 33/33；流水线改动 553/553、407/407 全部挂到 diff 上；没记录的改动只剩上下文里的 todo 重编号和摘要续写重建；每条记录计算 < 0.3 s

## 全项目审计（2026-11-10）
六个 subagent 分片通读（适配器 / service·store·server / 分析模块 / 改写审阅 / 前端 / 文档·配置·测试），每条结论复现或对着代码核对后才动。
回归测试：tests/test_audit_regressions.py（9 条）。

### 已修
- [x] TraceLab 同 id 样本互相覆盖（丢一条轨迹）；Claude Code 以 summary 行开头的会话丢 episode 关联；result 行 cost 为 null 清掉已知费用
- [x] ATIF 图片路径重复列出；没有 source_call_id 的 observation 被算成孤儿结果（样本变“未就绪”）
- [x] Jev 步骤扫描全部请求失败（服务不可用）时不再落缓存、不再标记为已分析（部分失败照旧缓存并提示，避免每次打开重复计费）；usage.input_tokens 为 None 不再崩；RetryPolicy 的 30 s 总预算让超时请求从不重试 → timeout=None
- [x] 错误模板里 URL 被路径规则吃掉；raw 导出把 CRLF 改成 LF
- [x] save_review 读-合并-写不在同一把锁里（并发保存互相丢字段）；导出名 `..` 能写到导出根目录之外
- [x] episode 视图 + Jev 筛选的 O(N²) 子查询（2 万行 33 s → 0.01 s）
- [x] 改写审阅：算不出 diff 的记录被分流成“可进”并导出；总览不显示方案条目数；“残留”卡片跳到轨迹库
- [x] 阅读器：窗口末尾按 j 两次或离开页面后无限请求 /messages；jumpTo 等待后不检查页面是否还在
- [x] 阅读记录：输入框里刚打的字被别的控件的保存结果盖掉；台账首次加载失败一直停在“读取台账…”
- [x] styles.css 多出的 `}` 让 .label-details 规则失效；“按当前筛选导出”漏掉 v4 筛选；盲判模式仍可按 v4 分数排序；episode 行显示的是主轨迹时长
- [x] 统计页混淆表不标 error→pass；从练习队列出来后导航一直高亮“每日练习”；Jev 面板写死“阈值 0.5”
- [x] 启动脚本用 PATH 上的 python3（没有 SDK 就起不来）→ 优先 .venv；`import` 相对路径报错；--no-text-index 帮助里的 1 KB → 0.3 KB
- [x] 删掉没人用的：adapters/base.py、OUTCOME_STATUSES、TranscriptBuilder.orphan_results、harness.merge / used_steps、Store.text_indexed、rewrite_review.changed、3 处 import json、.mono / .rr-records 样式
- [x] README：去掉本机绝对路径，补 export-jev、remove-collection、JEV_TIMEOUT、`?`；本文件 E1 测试数、C5、J1

### 待定（需要你拍板，见 2026-11-10 会话报告）
- [ ] 旧 registry.json 迁移：任何新的 --db 都会被灌入 “MoH runs”，删空后还会回来 → 删掉迁移还是加“已迁移”标记
- [ ] 服务器不校验 Host（DNS rebinding 可绕过 JSON content-type 防护）
- [ ] v4 奖励匹配的相关子查询：2 万轨迹 × 2000 奖励时每次列表 10 s
- [ ] 改写审阅：没有 id 的工具调用按位置编号导致整条对齐错位；摘要缓存键不含算法版本和相邻段；队列“继续加载”跳过改动；有 index_map 时删除的步骤显示成合并；改写侧没有工具声明时漏报残留；导出按旧字节偏移取行不核对样本 id；分流规则与 spec 不一致（人工“可进”压过排除、待定算已判）
- [ ] Jev：忽略报错的回看跳过了已经承认报错的纯文字步骤；并行调用只看前 3 个结果；缓存键不含模型；find_steps 全部失败时返回“无匹配”
- [ ] 规则信号：no_verify 把 latest / contest 当成跑了测试；infra_error 命中普通源码；harness_ref 算进“可疑成功”
- [ ] 每日练习队列同一天内不稳定（依赖行顺序和 Jev 结果）
- [ ] 适配器：MoH 是第二套 stream-json 解析（坏行/空字段整条导入失败，缺上下文/时钟轨）；Harbor 与 TraceLab 对“0 分 + 超时”的判法不同；Codex 工具名丢命名空间、结尾 reasoning 丢失；server_tool_use / 无 id 的 web_search_call 配不上结果；指纹不含评分文件；无时区时间戳按本地时区
- [ ] deploy.sh：pid 复用会杀错进程；5 s 不退出就丢 pid 文件；健康检查固定 127.0.0.1 + sleep 1
- [ ] 没有调用方的接口：POST …/scan、GET /api/compare、GET …/errors、api.deleteReview（界面删不了阅读记录）
- [ ] harness.trace 仍是平方级（2000 步 4 s；这次只去掉了每步重拼字符串，5.2 s → 4.0 s）
- [ ] 重新索引每个源重建一次 episode；batch_rows / stats / export_jev 整表加载；/api/collections 每次检查所有源路径
- [ ] 2026-09-04 的 spec 和 plan 描述的是已被取代的 v1；typesafe-sdk 没有上限版本；测试里约 50 个没关的数据库连接

## 阅读器与中英文界面（2026-11-11）
- [x] 阅读器：超长单行按字数折叠（输入也折叠）；Edit / MultiEdit / str_replace / Write / apply_patch 渲染为 diff；Jev 逐步扫描超过 300 个模型步骤不自动跑
- [x] 中英文界面：侧栏切换，选择存在浏览器里；源码里的中文是 key，英文在 web/locales/en/*.mjs；后端写的中文标签和句子用 tb() 在显示时翻译（backend.mjs）
- [x] 测试：tests/test_i18n.mjs（漏包 t()、缺英文、占位符不一致、同一文本两种译法、模块语法）、tests/test_i18n_backend.py（后端标签都有英文）
- [ ] 没翻译的：CLI 输出、导出的 Markdown 片段、Claude Code 压缩标记那一行转录文本、服务器报错信息（没有逐条查是否含中文）
- [ ] 英文单复数没处理（“calls 1 steps”）
- [ ] 未被要求动作的清单、行为变化的噪声判定：等 Evolvarium 的 3.0a / 3.0b 落地后接进就绪度和改写审阅
