# mysql_multi_source 全量 Review 报告（阶段18）

## 范围

- 前端：`index.html`（页面结构、交互逻辑、安全渲染、错误处理）
- 后端：`mysql_multi_source_main.py`（核心 API 参数校验、返回契约、任务链路）
- 文档：`README.md`、`docs/开发文档.md`、`docs/开发留痕.md`

## 结论总览

- 高风险问题（已修复）
  - 来源表格字符串拼接渲染存在 XSS 风险点。
  - 高频操作缺少防重复触发，存在并发提交和状态覆盖风险。
  - 前端对后端返回 `msg` 多态依赖重，错误提示不稳定。
- 中风险问题（已优化）
  - 结果反馈高度依赖 toast，信息易丢失。
  - 任务流程存在断点（创建任务后未自动回填 task_id）。
- 低风险问题（持续改进）
  - 单文件脚本体量大，后续可进一步按模块拆分为独立文件。

## 已落地修复

### 前端

- 新增统一请求适配与状态封装：`normalizeRes`、`singleFlight`、`reqKey` 顺序保护。
- 新增统一操作反馈：`showToast` + 固定结果面板 `#mms_result_panel`。
- 新增按钮执行态：`setBtnPending` / `runAction`，减少重复点击。
- 新增文本弹窗适配：`openTextDialog`，默认进行输出转义。
- 来源表格渲染改为转义输出（`esc/safeText`），堵住关键 XSS 注入面。
- 任务创建成功后自动回填 `boot_task_id`，打通下一步流程。

### 后端

- 新增统一返回辅助：`_ok` / `_fail`，开始收敛返回契约（`message/code`）。
- 新增 `source_id` 格式校验 `_validate_source_id`。
- `add_source` 增强：
  - `source_id` 合法性校验
  - `master_port` 数值和范围校验
  - 重复键冲突错误码化
- `set_db_mappings`、`create_bootstrap_task`、`trigger_bootstrap_task` 关键错误分支改为结构化返回。

## 风险与建议

- 当前仍是单文件前端，建议后续继续拆分为 `api/state/render/actions` 多文件（保持无构建链）。
- 仍有部分后端旧接口沿用字符串型 `msg`，建议在后续阶段逐步统一为结构化返回。
- 建议补充真实宝塔环境回归：高危操作、任务并发、无 mysql 客户端场景。
