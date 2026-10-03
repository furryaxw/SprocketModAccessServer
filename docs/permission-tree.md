# 权限树与资源说明

本文档描述当前后端注册的权限资源、动作节点以及对应的业务处理。
真源是各模块中的 `resources.register(...).add_perm(...)`。

## 1. 统一命名格式

System 和 Team 使用同一个格式：

```text
System.<resource>.<node>
Team.<team_id>.<resource>.<node>
```

代码和 API 使用小写 canonical node：

```text
System.<resource>.<node> -> system.<resource>.<node>
Team.<team_id>.<resource>.<node> -> team.<team_id>.<resource>.<node>
```

例如：

```text
System.Users.Read -> system.users.read
Team.<team_id>.Users.Read -> team.<team_id>.users.read
```

`System.*` 不是 `Team.system.*` 的别名：

- `System.*` 是平台级资源；
- `Team.system.*` 是保留 System Team 工作区中的 Team 资源投影。

### 节点分类

| 节点 | 含义 |
| --- | --- |
| `Read` | 读取资源，通常控制列表、详情、字段和页面区块。 |
| `Grant` | 分发同一资源的权限，不是资源业务动作。 |
| 其他节点 | 模块注册的业务动作，例如 `Manage`、`Approve`、`Distribute`。 |

模块作用域内注册的资源自动拥有 `Grant`；其他动作来自模块注册。手动注册的资源不会自动补权限；
中间路径，例如 `System.Authentication`，
只是分组，不能单独授予。

## 2. 权限树

方括号内是资源的完整动作集合。`<package_id>` 是运行时动态注册的 Package
资源。

```text
System
├ Authentication
│ ├ Me [Read, Grant]
│ │ ├ Authorization [Read, Grant]
│ │ └ Teams [Read, Grant]
│ ├ Session [Revoke, Grant]
│ └ TeamContext [Select, Grant]
├ Keys
│ └ Redeem [Distribute, Grant]
├ Operations
│ └ Status [Read, Grant]
├ Overview [Read, Grant]
├ Permissions [Read, Grant]
├ Platform
│ └ Config [Read, Grant]
├ Schema
│ └ Permissions [Read, Grant]
├ TeamApplications [Create, Read, Approve, Reject, Grant]
├ Teams [Read, ReadTeam, Manage, Confirm, Suspend, Activate, Archive, Grant]
└ Users [Read, ReadUser, Manage, SetPermissionTemplate, Confirm, Suspend, Activate, Grant]

Team
├ system
│ ├ Audit [Export, Grant, Read]
│ ├ Keys [Confirm, Distribute, Grant, Manage, Read, ReadBatch]
│ ├ Packages [Grant, Read]
│ ├ PermissionAssignments [Grant, Manage, Read]
│ ├ PermissionTemplates [Grant, Manage, Read]
│ └ Users [Activate, Confirm, Grant, Manage, Read, ReadUser, SetPermissionTemplate, Suspend]
└ template
  ├ Audit [Export, Grant, Read]
  ├ Self [Grant, Manage, Read]
  ├ Confirmation [Confirm, Grant]
  ├ Keys [Confirm, Distribute, Grant, Manage, Read, ReadBatch]
  ├ PackageUploads [Confirm, Create, Grant]
  ├ Packages [Download, Grant, Manage, Read]
  ├ PermissionAssignments [Grant, Manage, Read]
  ├ PermissionTemplates [Grant, Manage, Read]
  ├ Status [Activate, Archive, Grant, Suspend]
  └ Users [Grant, Invite, Read]
```

转换为 API/canonical node：

```text
System.Users.Read
  -> system.users.read

Team.<team_id>.Packages.Read
  -> team.<team_id>.packages.read

Team.<team_id>.Packages.<package_id>.Manage
  -> team.<team_id>.<package_id>.manage
```

## 3. System 资源

System 资源统一使用：

```text
System.<resource>.<node>
```

### 3.1 `System.Authentication`

这是分组路径，不是单独权限。真实资源如下。

#### `System.Authentication.Me`

canonical resource：`system.authentication.me`

| 节点 | 用途 |
| --- | --- |
| `Read` | 返回当前 session 用户、当前 Team、权限、effective/grantable permissions 和 tester_scopes。 |
| `Grant` | 分发当前用户资源权限。 |

子资源：

- `System.Authentication.Me.Authorization.Read`
  (`system.authentication.me.authorization.read`)：返回 system 权限、
  Team/content 权限、grantable 权限和 Assignment 快照。
- `System.Authentication.Me.Teams.Read`
  (`system.authentication.me.teams.read`)：返回当前用户可访问的、
  permission-derived 的 Team 列表。

#### `System.Authentication.Session`

canonical resource：`system.authentication.session`

| 节点 | 用途 |
| --- | --- |
| `Revoke` | 撤销当前 bearer session，并记录 `session.revoke` 审计事件。 |
| `Grant` | 分发 session 资源权限。 |

#### `System.Authentication.TeamContext`

canonical resource：`system.authentication.team_context`

| 节点 | 用途 |
| --- | --- |
| `Select` | 校验用户是否属于目标 Team，并返回该 Team membership。 |
| `Grant` | 分发工作区上下文资源权限。 |

`Select` 只改变请求上下文，不创建授权，也不替代后续资源权限检查。

### 3.2 `System.Platform.Config`

canonical resource：`system.platform.config`

- `Read`：返回平台运行模式，例如 `simple` 或 `complex`。
- `Grant`：分发平台模式资源权限。

### 3.3 `System.Overview`

canonical resource：`system.overview`

- `Read`：返回 Key、Grant、用户、Team、Package、申请、上传、健康和审计摘要；
  使用 `X-Team-Id` 时部分统计按 Team 过滤。
- `Grant`：分发 Overview 权限。

### 3.4 `System.Operations.Status`

canonical resource：`system.operations.status`

- `Read`：返回服务、数据库、存储和 schema 状态。
- `Grant`：分发运行状态读取权限。

当前处理器还要求调用者在 System Team 上具有 `users.read`。这是处理器内部的
访问前置条件，不会改变资源节点。

### 3.5 `System.Permissions`

canonical resource：`system.permissions`

- `Read`：返回权限目录和当前 Team 的权限模板。
- `Grant`：分发权限目录读取权限。

### 3.6 `System.Schema.Permissions`

canonical resource：`system.schema.permissions`

- `Read`：返回由资源注册派生的 `PermissionCatalog.permission_nodes`。
- `Grant`：分发权限节点目录读取权限。

### 3.7 `System.Users`

canonical resource：`system.users`

| 节点 | 用途 |
| --- | --- |
| `Read` | 列出平台用户。 |
| `ReadUser` | 读取单个用户详情。 |
| `Manage` | 组合更新用户状态与权限模板，单事务原子完成。 |
| `SetPermissionTemplate` | 给用户套用 System Team 权限模板。 |
| `Confirm` | 为用户操作发起确认 token。 |
| `Suspend` | 将用户状态设为 suspended。 |
| `Activate` | 将用户状态设为 active。 |
| `Grant` | 分发平台用户资源权限。 |

读取类处理器（列表、用户详情、系统权限读取）使用 `system.users.read`；
修改类处理器（`Manage`、`SetPermissionTemplate`、`Confirm`、`Suspend`、`Activate`）
使用 manage 路径：本人 → `user.manage` → `user.<id>.manage` → `system.users.manage`。

`ReadUser`、`SetPermissionTemplate`、`Suspend`、`Activate` 是注册出的节点，但当前
处理器不检查它们，只有 `Read` 与 `Manage` 参与判定；前端与权限模板不应把前四个
当作开关（见 `docs/frontend-page-spec.md` §5）。

状态变更的目标受限：`Suspend` 与 `Activate` 不接受当前会话自己的账户（403
`self_lockout`）与内置 `system` 账户（403 `system_account`）。

### 3.8 `System.Teams`

canonical resource：`system.teams`

| 节点 | 用途 |
| --- | --- |
| `Read` | 列出平台 Team。 |
| `ReadTeam` | 读取单个 Team。 |
| `Manage` | 修改 Team 名称和描述。 |
| `Confirm` | 为 Team 操作发起确认 token。 |
| `Suspend` | 将 Team 状态设为 suspended。 |
| `Activate` | 将 Team 状态设回 active。 |
| `Archive` | 将 Team 状态设为 archived。 |
| `Grant` | 分发平台 Team 目录权限。 |

读取类处理器要求 `system.teams.read`；修改类处理器要求
`system.teams.manage`。

### 3.9 `System.TeamApplications`

canonical resource：`system.team_applications`

| 节点 | 用途 |
| --- | --- |
| `Create` | 创建 Team 申请。 |
| `Read` | 读取待审核 Team 申请。 |
| `Approve` | 创建 Team、复制 Template Team 模板，并授予申请人 Owner。 |
| `Reject` | 拒绝申请并保存原因。 |
| `Grant` | 分发申请资源权限，不等于批准申请。 |

读取和审核处理器受 System Team 权限保护。批准成功后会动态注册新 Team 的
资源节点。

### 3.10 `System.Keys.Redeem`

canonical resource：`system.keys.redeem`

当前业务动作是 `Distribute`，canonical node 为
`system.keys.redeem.distribute`。它会：

1. 认证当前 session；
2. 要求显式 Team context；
3. 校验 `Idempotency-Key`；
4. 兑换 Key；
5. 返回本次 Grant、权限和用户 Grant 列表。

动作名沿用当前注册代码；业务含义是客户端兑换，不是管理端发行 Key。

## 4. Team 资源

Team 资源统一使用：

```text
Team.<team_id>.<resource>.<node>
```

`<team_id>` 的特殊值：

| ID | 含义 |
| --- | --- |
| `system` | 保留 System Team，提供系统工作区中的 Team 资源投影。 |
| `template` | 保留 Template Team，新 Team 的模板来源。 |
| 其他 ID | 普通运行时 Team。 |

三者使用同一套 Team 资源模型，但保留 Team 当前不一定注册普通 Team 的全部动作。

### 4.1 `Team.<team_id>.Self`

canonical resource：`team.<team_id>`

- `Read`：读取 Team 详情。
- `Manage`：修改 Team 名称和描述。
- `Grant`：分发 Team 详情/设置资源权限。

系统管理员可通过 `system.teams.manage` 管理 Team；普通 Team 管理员使用该
Team 的管理权限。

### 4.2 `Team.<team_id>.Confirmation`

canonical resource：`team.<team_id>.confirmation`

- `Confirm`：为 Team 操作签发确认 token。
- `Grant`：分发确认资源权限。

### 4.3 `Team.<team_id>.Status`

canonical resource：`team.<team_id>.status`

- `Suspend`：暂停 Team。
- `Activate`：把 Team 状态设回 active。
- `Archive`：归档 Team。
- `Grant`：分发状态资源权限。

保留 Team（`system`、`template`）不接受状态变更：暂停或归档后没有恢复接口，因此
`Suspend`、`Activate`、`Archive` 对这两个 team_id 一律以 403 `reserved_team` 结束。

### 4.4 `Team.<team_id>.Users`

canonical resource：`team.<team_id>.users`

- `Read`：读取 Team 成员列表。
- `Invite`：创建邀请，并绑定目标用户和 Team 权限模板。
- `Grant`：分发 Team 用户资源权限。

`Invite` 是邀请处理器的授权节点（System 级管理员亦可用 `system.teams.manage`）。

源码中还存在成员确认、修改成员模板、移除成员的函数，但当前没有通过
`add_perm` 注册，因此不是当前权限树节点。

### 4.5 `Team.<team_id>.Packages`

canonical resource：`team.<team_id>.packages`

- `Read`：读取该 Team 已发布 Package 的 manifest。
- `Manage`：修改指定 Package 版本的 metadata。
- `Download`：为已发布 Package 版本签发短期下载 token。
- `Grant`：分发 Package 资源权限。

Package 创建后，模块动态注册：

```text
Team.<team_id>.Packages.<package_id>.Read
Team.<team_id>.Packages.<package_id>.Manage
Team.<team_id>.Packages.<package_id>.Download
Team.<team_id>.Packages.<package_id>.Grant
```

canonical 形式为 `team.<team_id>.<package_id>.<node>`。
`Team.system.Packages` 当前只注册 `Read` 和自动生成的 `Grant`，用于读取跨
Team 的系统 Package catalog。

### 4.6 `Team.<team_id>.PackageUploads`

canonical resource：`team.<team_id>.package_uploads`

- `Create`：创建上传草稿并返回后端生成的 `permission_node` 与对象存储上传信息。请求不能提交 `permission_node`。
- `Confirm`：确认上传对象的 SHA-256 并发布 Package，发布后动态注册对应 Package 资源节点。
- `Grant`：分发 Package 上传资源权限。

### 4.7 `Team.<team_id>.Keys`

canonical resource：`team.<team_id>.keys`

| 节点 | 用途 |
| --- | --- |
| `Read` | 按状态、前缀和分页读取 Team Keys。 |
| `Manage` | 修改 Key 备注、权限、过期时间或状态。 |
| `Distribute` | 批量发行 Keys。 |
| `ReadBatch` | 读取发行 batch 统计。 |
| `Confirm` | 为 Key 操作发起确认。 |
| `Grant` | 分发 Key 资源权限。 |

当前写操作要求系统管理员权限，或目标 Team 上的 `keys.distribute`。

### 4.8 `Team.<team_id>.PermissionTemplates`

canonical resource：`team.<team_id>.permission_templates`

- `Read`：列出该 Team 的 Permission Templates。
- `Manage`：创建模板，并用 `PermissionCatalog.validate()` 校验权限节点。
- `Grant`：分发 Permission Template 资源权限。

`Team.template.PermissionTemplates` 是新 Team 初始化来源。创建新 Team 时，
模板记录被复制，节点中的 `team.template.` 前缀被替换成新 Team ID。

### 4.8 `Team.<team_id>.PermissionAssignments`

canonical resource：`team.<team_id>.permission_assignments`

- `Read`：读取该 Team 的授权快照。
- `Manage`：创建、更新、撤销、暂停、恢复和延长该 Team 的 Assignment；
  更新先 preview，删除权限或缩短有效期时按需确认。
- `Grant`：分发 Permission Assignment 资源本身的权限。

`Team.system.PermissionAssignments` 使用 users 模块的 system-permission 投影：

- `Read`：读取某用户的 system permissions；
- `Manage`：设置某用户的 system permissions；
- `Grant`：分发该资源权限。

### 4.9 `Team.<team_id>.Audit`

canonical resource：`team.<team_id>.audit`

- `Read`：按操作者、动作、目标、文本查询和分页读取可审计事件。
- `Export`：按相同过滤条件导出 CSV（系统 Team 覆盖跨 Team 事件；
  普通 Team 只导出本 Team 事件）。
- `Grant`：分发审计资源权限。

`team.system.audit` 是 System 工作区的跨 Team 审计投影；普通 Team 的
`team.<team_id>.audit` 只覆盖该 Team 事件。审计数据不新增 mutation，
只读查询与导出。

## 5. 保留 Team

### `Team.system`

当前注册：

```text
Team.system.Users
Team.system.PermissionAssignments
Team.system.PermissionTemplates
Team.system.Packages
Team.system.Keys
Team.system.Audit
```

这是 System 工作区的 Team 资源投影。平台目录和纯平台能力仍使用
`System.Users`、`System.Teams` 等 `System.*` 节点。

### `Team.template`

当前注册：

```text
Team.template.Self
Team.template.Confirmation
Team.template.Status
Team.template.Users
Team.template.Packages
Team.template.PackageUploads
Team.template.PermissionAssignments
Team.template.PermissionTemplates
Team.template.Keys
Team.template.Audit
```

这是新 Team 初始化时复制的来源，不是另一种权限模型。

### 普通 `Team.<team_id>`

普通 Team 创建后注册完整资源：Self、Confirmation、Status、Users、
Packages、PackageUploads、Keys、PermissionTemplates、PermissionAssignments
和 Audit。

## 6. Permission Template 与权限树
权限树定义系统有哪些资源和动作；模板只选择已有节点并写入用户 Grant：

```text
Permission tree:
  Team.<team_id>.Packages.Read

Permission template:
  includes Team.template.Packages.Read

Runtime grant:
  writes Team.<new_team_id>.Packages.Read
```

当前 seed：

- `seed/system.json`：System Team 的 `Owner`、`SuperAdmin`、`User`；
- `seed/team.json`：Template Team 的 `Owner`、`Admin`、`Developer`、`Tester`。

`SuperAdmin` 当前包含 `system.*`、`team.system.*`、`team.template.*`；
`User` 当前包含 `system.authentication.me.read`、`system.authentication.me.authorization.read`、
`system.authentication.me.teams.read`、`system.authentication.team_context.select`、
`system.authentication.session.revoke`；`Tester` 当前包含
`team.template.packages.read`。

模板实例**本身就是权限节点**，由后端生成并写进权限目录：

```text
team.<team_id>.templates.<template-id>          模板本身：拿到该节点＝拿到模板的权限集
team.<team_id>.templates.<template-id>.grant    允许把该模板分配给其他人
```

- 目录来源：`refresh_permissions()` 为每个 active 模板**注册一个 auto_grant 资源**并把它
  的节点本身显式并进 `PermissionCatalog.permission_nodes`（注册派生 `.grant`，实例节点来自
  数据）。模板新建、改名、停用后都会重建目录，所以这两类节点随模板生命周期出现和消失
  （见 `modules/permission_templates/instance_nodes.py`）。
- 末段用 `template_id`：段内非法字符替换为 `-`（节点段只允许 `[a-z0-9_-]`），同 Team 内
  冲突时按 `template_id` 顺序追加 `-2`、`-3` 去重。
- 授权里出现模板实例节点时，求值阶段把它**展开成模板成员节点**（`_expand_template_nodes`，
  按层展开并用已访问集合防环）。因此模板内容变化会立刻对已授权的用户/Key 生效，
  撤销该节点即撤销它带来的全部成员节点。
- 提交形态就是普通节点：`permissions` / `permissions_json` 里直接带模板实例节点，
  不需要单独的模板参数；`team.<id>.permission_templates.read/create/confirm/manage`
  仍旧是**模板管理页自身的权限**，与实例节点是两回事。

授权范围：`resource_grant_node()`（`modules/resource_handlers.py`，分配与模板两处共用）
对模板实例节点返回它自己的 `.grant`，所以"能否把这个模板分给别人"由
`...templates.<template-id>.grant` 决定；发行路径用 `require_grantable` 逐节点检查。

## 6.1 Effect（allow / deny）与优先级

- 每个 Assignment 有 `effect`（`allow` / `deny`）与 `priority`；求值时同一节点取最高
  priority，最高档里出现 `deny` 即否决（`domain/permissions.py:83-87`）。`deny` 就是
  **阻止**，用于覆盖模板或继承路径带来的 allow。
- 各写入路径当前的 effect 支持：

  | 路径 | 提交形态 | effect |
  | --- | --- | --- |
  | `system.users.permissions` manage | `{节点: effect}` | allow / deny |
  | `team.<id>.permission_assignments` manage（create / update / preview） | `{节点: effect}` | allow / deny；preview 返回真实的 `effect_changed` |
  | 权限模板 create / manage | 节点列表 | 仅 allow |
  | Keys 发行 distribute | 节点列表 | 仅 allow |

- 动作节点自动补同资源的 `read` 前置（`with_read_prerequisites`，补出来的是 `allow`）。

## 7. Public 注册资源

Public 资源由模块注册到运行时资源表，但不生成权限节点，也不写入
`PermissionCatalog.permission_nodes`。

| 资源 | Public action | 用途 |
| --- | --- | --- |
| `system.server_info` | `read` | 返回服务器协议、签名身份和认证能力。 |
| `system.key_status` | `read` | 返回公开签名 Key 状态。 |
| `system.authentication.github` | `exchange` | 用 GitHub access token 换取服务器 session。 |
| `system.authentication.github.device` | `start`, `poll` | 启动和轮询 GitHub Device flow。 |
| `system.authentication.github.web` | `start` | 启动浏览器 GitHub OAuth flow。 |

## 8. 页面投影与资源边界

权限树定义后端词汇，不等于每个 Team 工作区显示全部页面。页面、列表、字段和
操作都从同一份完整 `PermissionAssignment[]` 快照投影，后端仍负责最终的行和字段
过滤。

- System 工作区的 Permission Templates 使用 `team.system.permission_templates`。
- System Packages 只提供跨 Team catalog 读取，不提供 upload、preview、confirm 或
  publication mutation。
- Template Team 和普通 Team 不显示 Teams、Team Applications、Operations。
- Overview 在 System 中是跨 Team 摘要，在 Template/普通 Team 中是当前 Team 摘要。
- `team_members` 不是工作区、授权或用户列表的来源。

## 9. Assignment 生命周期

`permission_assignments.manage` 管理创建、编辑、暂停、恢复、撤销和延长有效期；
`permission_assignments.grant` 只负责分发 Assignment 资源权限。目标用户必须来自
服务端已有用户搜索，节点必须来自 Permission Catalog。

每次 mutation 使用：

```text
preview -> confirmation token -> Idempotency-Key mutation -> refresh -> audit
```

模板禁用立即撤销继承访问但保留审计历史。固定 Template Team 不可删除；删除按钮
只能执行受审计的禁用。

## 10. Package 和 Key 规则

普通 Team Package 使用 `read`、`manage`、`download`；`manage` 覆盖不可变
version 发布、metadata、status 和 configuration。System Package 只注册 catalog
read，客户端不能提交 `required_permission`，Package 节点由服务端生成。

同一 Package/version 只能有一个 archive。已存在版本必须在 preview 阶段阻止确认
和上传，即使 digest 相同也不能视为成功。Key 可携带多个独立 Assignment，明文只
返回一次，不得写日志或持久化。
