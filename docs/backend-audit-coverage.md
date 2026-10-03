# 后端审计覆盖面

概览页（`/overview`）的「最近活动」和 Audit 页（`/overview/audit`）都直接消费
`audit_events` 表，呈现结果取决于哪些模块写审计事件。

## 1. 写入审计事件的模块

写入统一走 `sprocket_access_server.modules.audit.store.SQLiteAuditStore.record`：

```python
record(*, actor: str, action: str, target: str,
       metadata: dict[str, Any], now: int, team_id: str | None = None)
```

当前全部调用点：

| 模块 | action | `team_id` | 调用点 |
| --- | --- | --- | --- |
| 邮件 outbox | `email.enqueue` / `email.sent` / `email.failed` | `None` | `infrastructure/messaging/email_outbox.py:28,51,62` |
| 系统 | `session.revoke` | `None` | `modules/system/resources.py:316` |
| Packages | `package.publish` | 当前 Team | `modules/packages/resources.py:183` |
| 权限模板 | `permission_template.create` / `.update` / `.disable` | 当前 Team | `modules/permission_templates/resources.py:449` |
| 权限分配 | `permission_assignment.create` / `.update` | 当前 Team | `modules/permission_assignments/resources.py:463` |

既定写入模式：`audit = _service(context, "audit")`（或
`context.services.get("audit")`），`if audit is not None:` 后写入；`actor` 用
`f"github:{actor}"` 或 `"system"`；`target` 用资源标识（`grant_id`、
`template_id`、`package_id@version`、`message_id`）；缺省时间用当前 Unix 时间。

`redact_metadata` 按精确键名匹配
`SENSITIVE_KEYS = {token, access_token, session_token, key, activation_key, secret, private_key}`；
`key_hash` 不在其中，不会被自动脱敏。

`team.confirm`、`team.member.confirm`、`key.confirm` 是**确认令牌**的 action
（`infrastructure/utilities/confirmations.py`），不是审计事件，不要混用命名。

## 2. 读取路径

- `modules/audit/store.py`：`search`（actor/action/target/query/limit/offset/team_id）、
  `count`、`export_csv`；`redact_metadata` 按 `SENSITIVE_KEYS` 脱敏。
- `modules/audit/resources.py`：注册 `team.<team_id>.audit`，权限 `read`（搜索 +
  分页 + 总数）与 `export`（CSV）。System Team 的节点跨 Team 查询（`team_id=None`），
  普通 Team 只查本 Team。
- `modules/system/resources.py:103`：Overview 载荷内嵌 `audit` + `audit_total`，
  按 `audit_actor`（服务端 actor/action/target 任一命中）过滤。

`team_id=None` 的事件（邮件、会话撤销）只出现在 System 工作区的审计里，
Team 工作区看不到。

## 3. 缺口

以下模块不引用 audit，因此不产生审计事件：

| 模块 | 缺失的事件 |
| --- | --- |
| `modules/team/resources.py` | 创建、改名、暂停、归档、成员邀请/移除（只发 `ResourceChanged` 广播） |
| `modules/applications/resources.py` | 申请创建、批准、拒绝 |
| `modules/keys/resources.py` + `modules/keys/store.py` | 发行（distribute）、兑换、撤销、管理更新 |
| `modules/users/resources.py` + `modules/system/provisioning.py` | 邀请、状态变更、权限模板变更、批量操作 |

## 4. 前端消费

- 概览页用 `OverviewPage.vue` 的 `auditLabels` 把已知 action 映射为可读文案；
  未收录的 action 显示原始值。
- Audit 页读取走 `team.<id>.audit.read` / `.export`。
