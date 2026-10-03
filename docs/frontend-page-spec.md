# Admin UI 页面与控件功能规格

**状态：当前前端基准**

本文档以当前 `admin-ui/src/` 源码为准，是管理前端的页面级功能规格。它补充并细化
[authorization-design.md](authorization-design.md)、[permission-tree.md](permission-tree.md)
和 [runtime-communication-contract.md](runtime-communication-contract.md)。

本文档回答以下问题：

- 页面在什么工作区显示；
- 页面顶部、列表、详情、弹窗和每个按钮显示什么；
- 每个控件需要什么权限；
- 点击或提交后调用哪个资源节点和 action；
- 成功、失败、取消、过期和权限变化时页面如何变化；
- 当前源码已经具备什么，以及还缺什么。

“当前实现”只表示源码中已经存在的行为，不表示浏览器验收已经完成。
“必须实现”是页面达到完整功能前不可省略的行为。任何按钮必须同时通过
前端可见性判断和后端权限检查；前端隐藏按钮不能替代后端拒绝。

## 1. 统一规则

### 1.1 全局工作区模型

工作区选择器只显示后端 `system.authentication.me.teams` 返回的工作区：

| 值 | 显示 |
| --- | --- |
| `workspace_kind=system` | System Workspace |
| `workspace_kind=team` | Team 名称 |

选择工作区必须调用：

```text
select system.authentication.team_context
data: { team_id }
```

成功后：

1. 保存 `team_id` 和工作区类型；
2. 刷新完整授权快照；
3. 刷新当前页面数据；
4. 如果当前路由在新工作区不可见，跳转 `/overview`；
5. 不重复切换工作区，不用旧请求覆盖新选择。

失败后保留上一个成功工作区，并显示错误 Toast。归档 Team 不得出现在选择器；
当前工作区被归档后，应清除选择并回到可用工作区或登录后的空工作区状态。

首次没有持久化选择时，默认候选顺序：存储的工作区（若仍有效）→ System
工作区 → 稳定顺序的第一个 Team。持久化选择只是偏好，必须按刷新后的
分配快照重新验证。

授权快照在以下时机刷新：登录/会话校验、工作区选择、任何成功 mutation、
401/403 响应、手动刷新、快照 TTL。

### 1.2 全局 Header

AppShell 的 Header 从左到右显示：

1. 移动端导航展开按钮；
2. 当前工作区名称；
3. 当前页面标题；
4. `LanguagePicker`；
5. GitHub 用户头像按钮。

语言按钮显示当前语言的短名称：

```json
{ "short": "EN", "name": "English" }
{ "short": "中", "name": "中文" }
```

点击后打开语言菜单；点击菜单项切换语言并立即更新页面文案、日期和状态标签；
点击菜单外关闭菜单。菜单外点击不得关闭业务弹窗。

用户头像必须为固定 `36x36`、圆形、`object-fit: cover`。有 GitHub user id 时使用
GitHub 头像；没有头像时显示用户名称首字母。点击头像打开菜单，菜单至少显示：

- 当前显示名称；
- GitHub user id；
- `Sign out / 退出登录`。

点击菜单外关闭用户菜单。点击退出后清除 session、关闭 WebSocket 并跳转登录页。

### 1.3 统一页面头

每页使用 `PageHeader`，从上到下显示：

- 页面类别或工作区范围；
- 页面标题；
- 一句页面职责说明；
- `Refresh / 刷新` 按钮；
- 页面主操作按钮。

Refresh 按钮：

- 显示 `Refresh / 刷新`；
- 请求期间禁用；
- 重新读取授权快照，再读取当前页面资源；
- 成功替换列表；
- 失败保留旧数据并显示错误状态和 Retry；
- 不改变工作区。

### 1.4 请求状态

所有页面和弹窗必须区分：

| 状态 | 显示 | 行为 |
| --- | --- | --- |
| `loading` | Loading | 首次加载时不显示旧列表 |
| `stale` | Refreshing | 有旧数据时保留旧列表并标记刷新中 |
| `empty` | No data | 显示空说明和页面允许的创建/刷新动作 |
| `forbidden` | 无权限 | 不显示受保护数据和操作 |
| `validation` | 响应格式错误 | 显示 Retry，不伪造空列表 |
| `server-error` / `error` | 服务失败 | 显示错误和 Retry |
| `cancelled` | 已取消 | 清理 loading，不覆盖后续请求 |

业务弹窗只能通过关闭按钮、取消按钮或完成操作关闭。点击遮罩空白处不能关闭；
提交期间关闭按钮和取消按钮禁用。所有 mutation 完成后刷新所属列表、授权快照和
受影响的工作区选项。

### 1.5 授权投影

前端只从一份完整 authorization snapshot 推导：

- 侧栏导航；
- 页面是否可见；
- 列表字段；
- 行操作；
- 弹窗主操作。

后端仍负责最终过滤行、字段和动作。至少满足以下规则：

- `read` 控制列表、详情、字段和页面区块；
- `manage` 控制资源修改，并依赖同一资源 `read`；
- `grant` 只控制分发权限，不替代业务动作；
- `distribute` 只控制 Key 发行；
- `X-Team-Id` 只是上下文，不是授权来源；
- 权限被撤销、过期或 deny 后，下一次快照刷新必须移除页面或按钮。

### 1.6 权限节点编辑器

权限节点一律用共享组件 `PermissionTreeEditor`（`admin-ui/src/components/`）编辑，
Templates、Assignments、Keys、Users 共用，页面不再各自实现节点选择。

注意：**树的内容完全来自后端返回的目录，前端不得自行拼接或注入任何条目。**
模板实例在后端就是节点（`team.<team_id>.templates.<slug>`，见
`docs/permission-tree.md` §6），随目录一起返回，和普通节点一样渲染、勾选、提交
（`read` 控制模板管理页自身的权限同样是目录里的普通节点）。

- **权限树**：由后端返回的扁平节点目录按 `.` 分段折叠成树；中间路径只是分组，
  不可单独授予；分组可展开/收起、可全选/清空（三态），叶子按 `read`、`manage`
  等动作名显示中文/英文短名并附完整节点。**折叠箭头要足够大可点，点击分组名称
  本身同样展开/收起该组的子节点**（复选框只负责选中）；
- **搜索框**只过滤树（标题与占位符是组件自带的「搜索节点」，不使用页面文案）。
  过滤无结果显示「没有匹配的节点。」并给出清除搜索，目录为空显示「没有可用的权限
  节点。」——两种状态必须区分，不能用页面的「—」代替；
- **已选权限节点**：已选集合就是 model 的 key 集合，每条 chip 可单独移除；不在当前目录
  中的节点标记 `目录外`；`effectsEditable` 的路径上每条 chip 还有 `允许 / 拒绝` 切换，
  拒绝的节点在树与 chip 上都用醒目标记（deny 即“阻止”）。
- **model 形态**：编辑器只有一个 `{节点: effect}` 映射，key 集合＝已选节点，effect 为
  `allow` / `deny`；这样“阻止权限”不需要额外并行状态。
- **手动添加节点**：独立小节（含一句用途说明）+ 输入框 + 添加。格式按后端
  `normalize_node` 校验（小写、每段 `[a-z0-9][a-z0-9_-]{0,63}`、通配符只能末段且
  最多一个），并要求包含动作段；重复节点直接拒绝；不在目录中的节点**允许添加但
  标记为目录外**并在组件内说明后端可能拒绝——客户端不代替后端做最终判定；
- 勾选动作节点时**由后端**自动补同一资源的 `read` 前置（后端不变量
  `with_read_prerequisites`，前端不再自行补节点）；前端只提交用户勾选的集合，保存后
  以服务端状态刷新显示。

## 2. 登录页 `/login`

### 页面职责

使用 GitHub 身份认证建立 session，成功后进入 `/overview`。登录页不需要工作区
选择器，不显示受保护的业务导航。

### 控件规格

| 控件 | 显示 | 可见/可用 | 行为 |
| --- | --- | --- | --- |
| SA 标识 | `SA` | 始终显示 | 纯品牌标识，不可提交 |
| LanguagePicker | `EN` 或 `中` | 始终显示，在 SA 右侧对齐 | 打开语言菜单；菜单外关闭；切换语言后保留当前登录页 |
| 主按钮 | `Continue with GitHub / 使用 GitHub 继续` | 未加载、无错误时显示 | 先读取 `system.server_info`，再按服务端能力选择 Web OAuth 或 Device Flow |
| Loading | `Loading...` | 登录流程中显示 | 禁用重复登录 |
| ErrorState | 错误消息 | 登录失败时显示 | 显示 Retry，重新执行登录流程 |
| Device verification link | `Open GitHub verification` | Device Flow 返回地址后显示 | 新标签页打开 GitHub 验证页 |
| Device code | `Enter code: <code>` | Device Flow 等待中显示 | 只读展示，用户在 GitHub 输入 |

### 登录流程

1. `read system.server_info` 获取 GitHub `web/device` 能力。
2. `web=true` 时 `start system.authentication.github.web`，打开授权弹窗。
3. OAuth callback 必须向 opener 发送成功或失败消息；前端验证 `event.origin` 和
   `event.source`，不能只验证同源。
4. `device=true` 时 `start system.authentication.github.device`，显示 code 和验证地址，
   按服务端间隔轮询 `poll`。
5. 得到 session token 后保存 token，读取当前用户并跳转 `/overview`。
6. session 校验失败时清除 token，保留登录错误并提供 Retry。

## 3. AppShell 全局页面

### 左上角 Logo 区

| 控件 | 显示 | 行为 |
| --- | --- | --- |
| Logo / Sprocket Access | 桌面端显示名称，折叠后只显示标识 | 点击切换侧栏展开/折叠 |
| 移动端展开按钮 | 菜单图标 | 打开覆盖式导航抽屉，不挤压内容区 |

### 左侧工作区与导航

工作区下拉列表显示后端返回的可访问工作区。导航项如下：

| 导航 | System 工作区 | 普通/Template Team |
| --- | --- | --- |
| Overview | `system.overview.read` | `team.<id>.overview.read` |
| Users | `system.users.read` | `team.<id>.users.read` |
| Teams | `system.teams.read` | 隐藏 |
| Permission Templates | `team.<id>.permission_templates.read` | 当前 Team |
| Permission Assignments | `team.<id>.permission_assignments.read` | 当前 Team |
| Packages | `team.system.packages.read` | `team.<id>.packages.read` |
| Keys | `team.<id>.keys.read` | 当前 Team |
| Operations | `system.operations.status.read` | 隐藏 |
| Applications | `system.team_applications.read` 或 `.create` | 隐藏 |
| Audit | `team.system.audit.read` | `team.<id>.audit.read` |

没有页面权限时隐藏导航项；直接输入 URL 仍必须由页面和后端拒绝。页面切换不应
自动清除已选工作区。

## 4. Overview `/overview`

### 页面职责

概览页回答三个问题：当前工作区**有没有需要我处理的事项**、**规模多大**、
**最近发生了什么**。页面区域按此顺序固定为：状态条 → 需要处理 / 服务健康 →
工作区规模 → 最近活动。

- System 工作区：平台级待处理事项、Team/用户/Package/Key/授权规模、服务健康、
  跨 Team 审计。
- 普通 Team：本 Team 的待处理事项、规模（用户口径为"持有有效授权的账户"）
  和本 Team 审计。

请求节点：

```text
System: system.overview / read
Team:   team.<team_id>.overview / read
```

请求数据：`audit_actor`（服务端按 actor/action/target 任一命中）、
`audit_limit`（1..100）、`audit_offset`。

### 状态条

| 控件 | 显示 | 行为 |
| --- | --- | --- |
| 范围 | `Scope / 范围` + 当前工作区名称 | 只读 |
| 待处理摘要 | `N 项需要处理` / `N 项待完成` / `没有待处理事项` | 只读；attention 条目优先，其次是 pending 条目 |
| 数据更新时间 | `数据更新于 <按语言格式化的本地时间>` | 每次成功读取后更新 |
| Refresh | `Refresh / 刷新` | 重新读取授权快照和 Overview |

### 需要处理

只列"有权限看到、且目标页面可处理"的条目；数量为 0 的条目不出现在列表中。
无条目时显示 `当前没有需要处理的事项。`。

| 条目 | 数量 | 可见条件 | 严重度 | 点击 |
| --- | --- | --- | --- | --- |
| Team 申请待审 | `applications.pending` | System 且 `system.team_applications.read` 或 `.create` | attention | `/overview/applications` |
| 上传已超时 | `uploads.timed_out` | System：`system.operations.status.read`；Team：`package_uploads.create` 或 `packages.manage` | attention | `/overview/packages` |
| 授权已过期 | `grants.expired` | `permission_assignments.read` | attention | `/overview/permission-assignments?status=expired` |
| 上传待完成 | `uploads.pending` | 同上 | pending | `/overview/packages` |

Packages 页当前没有上传草稿状态视图，因此上传类条目只做页面定位，不带
`upload_status` query。

### 服务健康

仅 System 工作区、拥有 `system.operations.status.read`、且响应含 `health`
时显示，与"需要处理"并排；Team 工作区不显示该区块，"需要处理"占满整行。

Service / Database / Storage 三项，状态为 `OK / Configured / Unavailable /
Error`；状态为异常（`error` / `unavailable`）的条目自身链接到 Operations。

### 工作区规模

按权限显示的规模计数，每张卡片可点击进入对应页面：

| 指标 | 数值 | 可见条件 | 点击 |
| --- | --- | --- | --- |
| Keys | `keys.total`，副文案为未使用数量 | 对应 Keys read | `/overview/keys` |
| 授权 | `grants.total` | Assignment read | `/overview/permission-assignments` |
| 用户 / 授权用户 | `users`（Team 口径为持有有效授权的账户数） | Users read | `/overview/users` |
| 团队 | `teams` | System 且 Teams read | `/overview/teams` |
| Packages | `packages` | Package read | `/overview/packages` |

待处理类数字（待审申请、超时/待完成上传、过期授权）只在"需要处理"区出现一次，
不在规模区重复。

### 最近活动

- 显示时间、动作、操作者、目标；服务端分页，每页 8 条；
- 已知动作显示可读文案并保留原始 `action`，未收录的动作直接显示原始 `action`；
- 失败类事件（`action` 以 `.failed` / `.error` 结尾）显示异常样式；
- 标题右侧显示匹配总数 `共 N 条`；有 `audit.read` 时提供"查看全部审计"入口；
- 搜索框按 actor/action/target 过滤，输入 debounce 300ms，过滤后回到第 1 页；
- 向前、向后翻页都重新取数（包括从第 2 页退回第 1 页）；
- 点击事件打开详情弹窗：动作（原始 `action`）、操作者、目标、时间和
  metadata JSON；点击遮罩不关闭，只有 Close。

时间必须按当前语言格式化；缺失时间显示 `—`，Unix `0` 不是正常创建时间。

## 5. Users `/overview/users`

### 页面职责

- System 工作区：平台用户目录；查看详情、修改状态与权限模板、批量状态变更。
- Team 工作区：由有效 Team 权限推导出的成员目录（只读）+ 邀请成员。

不能用 `team_members` 表作为可见性来源。

### 授权节点（按实际强制）

| 操作 | 节点 |
| --- | --- |
| 列表读取、用户详情、系统权限读取 | `system.users.read`（System）、`team.<id>.users.read`（Team） |
| 状态变更、权限模板、编辑保存 | manage 路径：本人 → `user.manage` → `user.<id>.manage` → `system.users.manage` |
| 邀请成员（Team） | `team.<id>.users.invite`；System 级管理员亦可用 `system.teams.manage` |
| 读取权限模板选项 | `team.system.permission_templates.read` / `team.<id>.permission_templates.read` |

渲染不使用 `system.users.read_user`、`.suspend`、`.activate`、
`.set_permission_template`、`system.users.permissions.read` 作为开关：这些节点已注册
但没有处理器检查它们，按它们开门会出现"亮着但必被拒绝"的按钮。

### 状态条

范围（工作区名）、总数（`N 个用户` / `N 名成员`）、有过滤时的 `已应用过滤`、
数据更新时间。页面头为 `Refresh` 和 `Invite member`（仅 Team 且有 invite）。

### 过滤与列表

- 过滤：GitHub login、display name、stable user id；输入 debounce 300ms 并回到第 1 页；
- 分页：服务端 limit/offset，页脚显示上一页/下一页与 `第 X / Y 页`；
- 列按工作区区分，只显示后端载荷真实提供的字段：
  - System：用户（显示名 + user id）、状态、创建时间、最近登录、操作；有批量能力时
    增加选择列；
  - Team：用户、状态、权限来源（`source_type/source_id`）、加入时间。
- 有过滤且无结果时显示 `没有匹配的用户`，与空目录区分。

字段缺失显示 `—`，不能显示 `undefined`、`null` 或 Unix `0`。

### 行操作（System）

| 按钮 | 显示条件 | 行为 |
| --- | --- | --- |
| Details | 能读列表即可 | `read_user system.users` 与 `system.users.permissions` 读，两个请求各有 loading/empty/error/Retry |
| Suspend / Activate | 目标状态与按钮相反，且该行可管理 | confirm 令牌 + `suspend` / `activate system.users`，成功刷新列表与授权快照 |
| 权限节点 | 该行可管理 | 打开「设置系统权限节点」弹窗 |

Team 工作区没有行操作：用户管理处理器强制 System Team 上下文。

自管理规则：用户默认可以修改自己的状态和模板；修改其他用户
需要 `user.manage`、`user.<id>.manage` 或 `system.users.manage`。不可管理的行保留
按钮但禁用并在 `title` 给出原因；只有自管理能力时，表格上方显示一行只读说明，
不靠隐藏按钮表达限制。

### 批量状态变更（System）

- 选择列只在具备"管理他人"能力时出现，不可管理的行复选框禁用；
- 批量暂停/启用先显示确认弹窗（动作、目标列表、数量）；
- 执行是逐用户串行 confirm + 状态变更，**首个失败即停止**，报告已更新数量与失败原因，
  随后无条件刷新列表并清除选择；
- 不得在部分失败时给出"整体成功"的提示。

### Details 弹窗

显示名称、GitHub user id、状态、创建时间、最近登录；下方独立列出系统权限与授权
来源（node/value、effect、来源、优先级、有效期），无数据时显示明确空态。

| 按钮 | 行为 |
| --- | --- |
| Close | 关闭并清理 detail/permission 请求状态 |
| Retry | 只重试失败的那一个请求 |

### 设置系统权限节点弹窗

行内「权限节点」按钮打开，直接用共享 `PermissionTreeEditor`（§1.6）编辑该用户的
**系统级权限节点**。用户侧没有模板路径：模板的创建/编辑在权限模板页，Team 邀请
选择已有模板。

- 读取：`system.users.permissions` read，返回的 `permissions` 每项含 `value` 与
  `effect`；树节点集合即这些 `value`；
- 可选节点：`system.schema.permissions` read 返回的目录，过滤为 `*`、`system.*` 与
  System Team 投影 `team.system.*`——与后端读取条件（`grant.team_id IS NULL` 且节点
  为 `*`、`system.%` 或 `team.system.%`）一致，避免勾了却读不回来；
- 保存：`manage system.users.permissions`，提交 `{user_id, permissions: {node: effect}}`；
  **整体替换语义**，已有节点的 effect 从读取结果回填，新增节点用 `allow`；模板实例节点
  就是普通节点，直接提交即可，后端在求值时展开成模板成员节点；
- 读取未成功前不显示树且禁用保存，避免用空集合覆盖现有节点；
- 已知依赖：目录读取（`system.schema.permissions`）实际要求 `team.system.users.read`；
  只持有 `system.users.read` 的角色能读节点但拿不到目录，此时树为空并给出错误提示。

### Invite 弹窗（Team）

字段：GitHub user ID、权限模板（下拉，选项来自当前 Team 的模板；读不到时自由文本）。

`Send invite` 必须要求两个字段非空，调用：

```text
invite team.<team_id>.users
data: { user_id, permission_template }
```

成功后关闭弹窗、刷新 Team 用户列表和授权快照；失败保留输入并显示错误。

## 6. Teams `/overview/teams`

### 页面职责

只在 System 工作区显示平台 Team 目录。Template Team 是固定受保护实例，不得删除；
归档 Team 不得继续出现在工作区选择器。

### 列表字段

- Team name 与 team id；
- Status：Active、Suspended、Archived；
- Owner；
- Member count；
- Created；
- Actions。

### 行按钮

| 按钮 | 显示条件 | 行为 |
| --- | --- | --- |
| Details | `system.teams.read_team` | `read_team system.teams`，显示详情 |
| Edit | `system.teams.manage` | 打开名称和描述编辑弹窗 |
| Suspend | manage 且当前 Active | `suspend system.teams`，需按统一确认流程 |
| Archive | manage 且未 Archived | `archive system.teams`，需确认；成功后刷新 Team 列表和工作区选项 |

Archive 不能只是从列表删除。后端必须把状态写为 `archived`，撤销可访问 Team
的派生可见性；前端收到 Team 顶层资源事件后刷新工作区列表。已归档 Team 的旧
页面请求必须返回不可用/无权限，不能显示空壳页面让用户继续操作。

### Details 弹窗

显示 Team name、id、owner、status、description、created_at、updated_at、member
count，以及授权/初始化摘要（如果权限允许）。按钮只有 Close 和失败时 Retry。

### Edit 弹窗

字段：

- Team name，必填；
- Description，可空。

`Save` 调用 `manage system.teams`。成功后刷新 Team 目录、工作区选项、当前标题；
若修改的是当前 Team，不能触发工作区震荡。

## 7. Permission Templates `/overview/permission-templates`

### 页面职责

管理当前 Team 自己的 Permission Template。System 工作区使用
`team.system.permission_templates`；Template Team 和普通 Team 使用自己的 Team 节点。
Template 不是运行时角色，修改 Template 不修改已有直接 Assignment。

### 列表字段

- Name 与 template id；
- Status：Active、Disabled；
- Permission count；
- Expiry：Permanent 或天数；
- Created by；
- Created；
- Actions。

### 页面按钮

| 按钮 | 显示条件 | 行为 |
| --- | --- | --- |
| Refresh | 当前 Team template read | 刷新列表 |
| Create template | template manage | 打开创建弹窗 |
| Edit | template manage | 打开编辑弹窗 |
| Enable | manage 且 Disabled | `manage`，设置 `status=active` |
| Disable | manage 且 Active | 先 `confirm`，再 `manage status=disabled` |
| Delete | manage 且 Active | 文案是 Delete，但语义是审计保留的禁用；先 confirm，再传 `delete=true` |

固定 Template Team 不得提供物理删除。Delete 按钮必须明确显示“禁用并保留审计记录”，
不能让用户误以为数据库行会消失。

### Create/Edit 弹窗

字段：

- Template name，必填；
- Permission nodes：用共享 `PermissionTreeEditor`（§1.6）选择或手动添加；
- Expiry in days，可空，必须是正整数；
- 节点必须能解析到已注册资源；目录外的节点会被后端拒绝；
- 需要额外动作时自动包含对应 `read` 前置节点。

创建调用 `create`；成功后刷新列表和授权快照。编辑调用 `manage` 的 update；
权限减少、有效期缩短或禁用必须走：

```text
confirm -> confirmation_token -> manage
```

提交期间锁定字段。失败时保留输入，显示后端验证的具体节点和原因。

## 8. Permission Assignments `/overview/permission-assignments`

### 页面职责

这是授权工作台，不是只读日志。它管理当前 Team 范围内由权限允许管理的
Permission Assignment，展示来源、effect、优先级、grant effect、有效期和生命周期。

### 过滤和分组

顶部显示：

- User filter；
- Permission node filter；
- Source/provenance filter；
- Status filter：Active、Suspended、Revoked、Expired；
- Expiry filter；
- Clear filters；
- 分页。

列表按用户分组。每组显示 user id、用户显示名称（若可读）和 assignment 数量。
服务端必须先按请求者权限过滤，再执行查询和分页。

### 行字段

- Node；
- Source / provenance；
- Effect：Allow、Deny；
- Priority；
- Grant effect；
- Status；
- Expires at；
- Created at；
- Actions。

所有时间统一格式化；无有效期显示 `Never`。`grant_effect=deny` 必须清晰显示，
不能与普通 effect 混淆。

### 行按钮

| 按钮 | 显示条件 | 行为 |
| --- | --- | --- |
| Edit | manage 且未 Revoked | 编辑权限集合和有效期 |
| Suspend | manage 且 Active | 先 preview/confirm，再 suspend |
| Restore | manage 且 Suspended | restore |
| Revoke | manage 且未 Revoked | 先 confirm，再 revoke |
| Extend | manage 且未 Revoked | 打开有效期弹窗 |

Owner、当前用户、最后必要 Owner、受保护资源等限制由后端返回；前端应显示禁用
原因而不是在请求后才静默失败。

### Create 弹窗

字段：

- Target user id，必填，必须来自可见用户；
- Permission nodes：用共享 `PermissionTreeEditor`（§1.6）选择或手动添加；
- Expires at，可空，使用 Unix 秒输入控件或日期时间选择器，不允许模糊文本。

`Create` 必须先验证每个节点的 catalog、read 前置、grantability、priority、
effect 和目标用户可见性，然后用 `manage` 创建。成功后刷新 Assignment 列表和
授权快照；失败保留输入。

### Edit 弹窗

打开时显示 assignment id、用户、node、source、当前 effect 和权限集合。用户只能
编辑后端允许修改的字段。提交流程：

1. `manage preview=true`；
2. 如果删除权限或缩短有效期，调用 `manage mode=confirm`；
3. 使用一次性 `confirmation_token` 和新的 `Idempotency-Key` 调用 `manage`；
4. 成功后刷新列表、授权快照和审计。

Preview 的差异必须显示给用户：删除的节点、effect 变化、priority 变化、有效期
缩短和受影响用户。

### Lifecycle 弹窗

Suspend、Revoke 和其他破坏性操作必须在确认内容中显示：

- 目标用户；
- assignment id；
- 当前状态；
- 将失去的节点；
- 是否影响工作区或页面访问；
- 确认和取消按钮。

重复使用相同 `Idempotency-Key` 返回原响应；不同请求不得复用同一 key。

## 9. Packages `/overview/packages`

### 页面职责

System 工作区只读跨 Team Package catalog；普通 Team 管理当前 Team 的 Package。
System 不显示上传、发布和 metadata mutation 按钮。

### 列表字段

- Package id/name；
- Version；
- Status；
- Size；
- Digest；
- Published；
- Actions。

`published_at` 必须是数字 Unix 时间戳并按语言格式化。空值显示 `—`；新创建的
Package 不得因为省略时间被写成 Unix `0`。

### 页面按钮

| 按钮 | 显示条件 | 行为 |
| --- | --- | --- |
| Refresh | Package read | 刷新当前范围 |
| Upload Package | 普通 Team 且 `package_uploads.create` | 打开上传弹窗 |
| Download | Package-specific download 或 Team download | 请求短期 token 后下载 archive |
| Manage | 普通 Team 且 Package manage | 编辑 metadata |

System Package catalog 的 Download 必须仍受具体 Package/Team download 权限保护；
有 manage 没有 download 不能下载 archive。

### Upload 弹窗

字段：

- Package ID：小写单段、点号或连字符格式；
- Version：SemVer；
- Archive file；
- 文件大小和类型摘要；
- digest 计算状态。

提交流程：

1. `create team.<id>.package_uploads`，服务端生成 `permission_node` 和 upload id；
2. 使用服务端返回的 upload URL 上传文件。Local storage 使用 HTTP PUT，
   不得生成 `file://`；
3. 浏览器计算 SHA-256；
4. `confirm team.<id>.package_uploads`，提交 upload id 和 digest；
5. 成功后 Package publication、Package 资源注册、列表刷新和 toast 同步完成。

界面状态必须依次显示：

```text
Creating upload draft
Uploading archive
Confirming publication
Package published
```

已存在的 Package/version 必须在 preview 或 create 阶段阻止，不得因 digest 相同
而覆盖不可变 archive。失败时回到可重试状态，但不能重复创建一条成功发布记录。

### Download

调用 Package-specific node 的 `download`，取得短期 token 和 download path；
再发起下载。token 不能写日志、localStorage 或页面永久状态。失败显示明确的
权限、版本不存在、Package disabled 或 token 错误。

### Manage metadata 弹窗

显示可编辑 JSON metadata。保存前必须校验：

- 是合法 JSON；
- 顶层是 object，不是 array；
- 不允许修改 package id、version、archive digest、archive size；
- 必须保留生成的 release 和 published_at。

保存调用 `manage team.<id>.packages`，成功后刷新列表和 Package 详情。metadata
写失败时保留原 JSON 和错误位置。

## 10. Keys `/overview/keys`

### 页面职责

显示当前工作区 Key inventory，并允许有权限的用户发行、读取 plaintext、
修改备注或撤销未使用 Key。

### 统计区

显示每种状态数量：

- Unused；
- Redeemed；
- Expired；
- Revoked。

统计数字必须与当前过滤范围一致，不能只显示未过滤总数。

### 列表字段和按钮

字段：

- Key id；
- Plaintext（明文随库存保存；发行前写入的旧 Key 为空）；
- Status；
- Permission count；
- Expires；
- Created；
- Note；
- Actions。

| 按钮 | 显示条件 | 行为 |
| --- | --- | --- |
| Issue Keys | `team.<id>.keys.distribute` | 打开发行弹窗 |
| Manage | `team.<id>.keys.manage` | 编辑备注和允许的状态，查看明文 |
| Copy | 该行有 plaintext | 复制明文 |
| Download | 发行成功后的弹窗内 | 下载本地 txt，不上传 |

已 Redeemed/Expired 的 Key 不能回到 Unused。撤销操作必须确认并记录审计。

### Issue 弹窗

字段：

- Quantity，正整数；
- Permission nodes：用共享 `PermissionTreeEditor`（§1.6）选择或手动添加；
- 可选有效期；
- 发行前预览完整 Assignment 集合、priority、effect、grant effect 和范围。

流程：

```text
preview -> confirmation -> distribute team.<id>.keys -> plaintext returned and stored
```

成功后：

1. 表格和统计刷新；
2. 弹窗显示明文并提供复制/下载；
3. 明文已持久化，关闭弹窗后仍可在列表或 Manage 弹窗中读取。

发行失败不得消耗 Key 或产生部分 Assignment。

### Manage 弹窗

可编辑备注和允许的状态。提交后刷新列表和统计。后端必须拒绝已经兑换、过期或
受保护 Key 的非法状态转换。

## 11. Applications `/overview/applications`

### 页面职责

只在 System 工作区显示 Team application。申请者如果只有 create 权限，可以看到
创建入口，但不能伪造审核列表。

### 列表字段

- Name 与 team id（id 为服务端派生值，只读展示）；
- Applicant；
- Status：Pending、Approved、Rejected；
- Submitted；
- Actions。

只对 Pending 行显示操作：

| 按钮 | 权限 | 行为 |
| --- | --- | --- |
| Approve | `system.team_applications.approve` | 预览将创建的 Team、Owner、Template clone 和资源注册；确认后 approve |
| Reject | `system.team_applications.reject` | 打开拒绝原因弹窗，提交 reject |
| Submit application | `system.team_applications.create` | 打开创建弹窗 |
| Refresh | read 或 create | 刷新列表（只有 read 才能读取列表） |

Approve 必须原子完成：

- application 状态；
- Team 创建；
- Template Team 快照复制；
- Owner assignment；
- 新 Team 资源注册；
- 工作区列表广播和前端刷新。

成功后新 Team 必须出现在工作区下拉列表。点击新 Team 只能产生一次
`select team_context`，不得在旧/新 Team 间震荡。

### Create 弹窗

字段：

- Team name，必填；
- description，可空。

`Submit` 调用 `create system.team_applications`。成功后清空表单、关闭弹窗并在
申请者可读时刷新列表。提交负载只携带名称与描述：Team id 由服务端按名称派生（人类可读、
不含 `.`），批准时在已占用 id 上追加 `-2`、`-3` 后缀，待审期间同名申请只保留一份。

### Reject 弹窗

显示申请名称、id 和 rejection reason 输入框。`Confirm rejection` 必须提交 reason；
成功后关闭并刷新。关闭或失败不改变申请状态。

## 12. Operations `/overview/operations`

### 页面职责

只在 System 工作区显示服务运行状态，不能成为普通 Team 的隐式健康接口。

### 列表

每行显示：

- Component；
- Status：OK、Configured、Unavailable、Error；
- Detail。

组件至少包括 service、database、storage、schema。状态颜色和文字必须同时表达，
不能只依赖颜色。

### 控件

| 控件 | 行为 |
| --- | --- |
| Refresh | 重新读取 `read system.operations.status` |
| Retry | 请求失败后重试 |
| Cancel | 仅请求支持取消时显示 |

Operations 当前是只读页面，不显示 Restart、Repair、Delete、Migrate 等不存在的
按钮。Detail 不能泄露 secrets、tokens、签名私钥或 OAuth 凭据。

## 12.5 Audit `/overview/audit`

### 页面职责

System 工作区使用 `team.system.audit` 展示跨 Team 审计事件；普通 Team 使用
`team.<team_id>.audit` 展示本 Team 事件。审计是只读查询 + CSV 导出，没有
mutation 按钮。

### 列表字段

- Time：创建时间，按当前语言格式化，缺失显示 `—`；
- Actor：操作者；
- Action：动作；
- Target：目标；
- Details：打开事件详情弹窗。

### 控件

| 控件 | 显示条件 | 行为 |
| --- | --- | --- |
| Refresh | `team.<id>.audit.read` | 重新读取审计列表 |
| Search | `team.<id>.audit.read` | 搜索操作者、动作或目标（服务端过滤，带 debounce） |
| Export CSV | `team.<id>.audit.export` | 按当前过滤条件导出 CSV，本地下载，不持久化服务端 |

### 详情弹窗

显示时间、操作者、动作、目标和 metadata JSON。按钮只有 Close。

## 13. 当前源码差异清单

以下记录各区域当前仍待验收或待办的行为。「待验收」表示需要真实浏览器/运行环境
确认，不表示静态源码缺失。

| 区域 | 仍待验收 / 待办 |
| --- | --- |
| 全局 Shell | 导航图标按页语义、部分键盘焦点细节 |
| 登录 | 真实 OAuth provider 交互 |
| Overview | 审计覆盖面：Team 生命周期、Key 发行、用户管理当前不产生审计事件，活动列表只反映已记录的模块（见 `docs/backend-audit-coverage.md`）；真实浏览器交互验收 |
| Users | 真实浏览器交互验收；多角色矩阵（Team Admin / 只读 / 仅 `system.users.manage`）验证 |
| Teams | 真实浏览器交互验收 |
| Templates | 真实浏览器交互验收 |
| 共享编辑器 | 真实浏览器交互验收（展开/全选/手动输入/移除的键盘与焦点行为） |
| Assignments | 服务端 preview effect/priority 变化（当前编辑不修改这两个字段） |
| Packages | upload 状态查询与 Overview upload_status 联动、真实浏览器验收 |
| Keys | 后端发行无确认令牌环节（保持现有幂等发行流程） |
| Applications | 真实浏览器交互验收 |
| Operations | 敏感信息过滤（依赖后端）、真实浏览器验收 |
| Audit | 真实浏览器交互验收 |
| 通用状态 | 焦点与可访问性细节 |

## 14. 最小验收矩阵

实现每一页后至少验证：

1. 无页面 `read` 时导航和直接 URL 都不可用；
2. 有 `read` 无业务 action 时能看列表但看不到对应按钮；
3. `manage` 无 `download` 时 Package 可编辑但不能下载；
4. 权限撤销、过期或 deny 后刷新快照，页面和按钮立即消失；
5. 工作区切换只发起一次选择请求，旧请求不能覆盖新选择；
6. 新建或归档 Team 后工作区列表实时更新；
7. 所有省略的创建/申请/发布时间使用当前 Unix 时间，显式测试时间（包括 `0`）
   仍保持确定性；
8. 所有弹窗点击遮罩空白处不关闭；
9. 所有破坏性 mutation 使用 preview、confirmation token 和 Idempotency-Key；
10. 所有成功 mutation 刷新资源列表、授权快照、必要时刷新工作区选项；
11. 所有时间字段按当前语言格式化，缺失值显示 `—`，不显示 `undefined`；
12. 浏览器断线、响应格式错误、403 和 服务器错误均有可操作恢复路径。

