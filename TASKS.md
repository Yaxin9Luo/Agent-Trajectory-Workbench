# 推进计划 — 全部完成

## Phase 1: 修 bug
- [x] 1a. `get_run` 返回完整 messages 浪费带宽
- [x] 1b. tool 过滤与 role 过滤逻辑不一致
- [x] 1c. result 行 text 用 str() 而非 JSON
- [x] 1d. appendToolImages 不去重
- [x] 1e. terminalCards exit_code 字符串比较

## Phase 2: Jev 语义分析集成
- [x] 2a. 分析问题集（health_score / needs_human / error_severity / failure_category）
- [x] 2b. analysis.py — 组装 state + 调 Jev + 缓存
- [x] 2c. `/api/runs/<id>/analysis` 端点
- [x] 2d. 前端"Jev 语义分析"面板 + 置信度展示
- [x] 2e. 测试通过（test_analysis.py 3 tests）

## Phase 3: 高价值缺失功能
- [x] 3a. 错误/重试聚合视图（后端 + 前端）
- [x] 3b. artifact.html 内容 diff（后端 + 前端）
- [x] 3c. 跨 run 对比（后端 + 前端）

## 部署
- [x] 更新 README（依赖声明 + 新功能）
- [x] 用真实轨迹（prov demo show 8 runs）注册到本地 registry
- [x] 启动 localhost 验证 — http://127.0.0.1:8877

## 测试
- 43 Python tests pass (1 skip: real-run smoke)
- 7 Node rendering tests pass
- Jev 分析在真实 run 上端到端验证通过（~1.4s）
