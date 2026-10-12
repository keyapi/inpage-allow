---
type: Reference
title: 受控办理助手交接
---

# 受控办理助手交接

先读 [本次交接与续办清单](docs/handoff-2026-10-12.md)、[规格](docs/competition-plan.md)、[边界](docs/security-boundaries.md)、[剧本](docs/demo-script.md)、[静态配置暴露经验](docs/solutions/static-config-exposure.md)。独立竞赛仓库不读取公司环境凭证，不连接真实银行系统。

目标：`server.py` 负责 HTTP 会话、同源检查、请求标记、静态白名单、调用限制和模型循环。当前这些新HTTP能力尚未实现，只已部署安全封堵。`domain.py` 已实现模拟资料、配置、提案、权限、SQLite 事务和审计，但尚未接入HTTP。新界面是未联调草稿。运行使用 `uv run python server.py`，验证使用 `uv run python -m unittest discover -s tests -v`。

## 保留的设计

- 身份来自后台会话，不能信任请求角色、菜单隐藏或模型自报权限。
- 有效操作范围是用户权限、工具能力、请求范围、业务条件的交集。
- 确认绑定不可变提案摘要、十分钟有效期、状态版本；参数变化需重新确认。
- 生产仅形成申请，复核也不产生生产配置写入。
- 配置和工单写入使用事务，实际结果重新读取；失败回滚不得显示成功。
- 金额以元保存，初始 500000 元（50 万元）；超时以秒、重试以次数存储。
- 资料均模拟；有效版本、适用环境和引用可见。资料不能授予执行权限。
- page-agent 仅保留历史调研，不能恢复公开代理或通用执行工具。
- 演示登录不是 BOC SSO，SQLite 日志不是防篡改监管审计。

环境见 [.env.example](.env.example)。禁止提交 Key、数据库、截图和运行数据。用户拒绝本次轮换 Key，不能宣称历史暴露风险消失。Git 走分支和 PR。根索引用 `uv run python scripts/update_index.py` 自动同步，不跨仓库修改。
