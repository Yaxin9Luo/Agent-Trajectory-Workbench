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
- [x] J1 逐步扫描（阶段 + 8 个 Noul，按证据可用性提问；截断/图片时不判“误读”）——真实轨迹抽查后两轮修正
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
- [x] E1 单元测试（适配器、信号、Jev 假客户端、练习、服务、服务器、前端安全）— 76 Python + 12 Node
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
- [ ] C5 minimap、长输出智能折叠/同命令 diff、按需加载图片

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
