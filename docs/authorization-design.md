# 授权设计

Access Server 只使用一套显式的权限节点评估器。Owner、Admin、Developer、Tester 等名称只是 Permission Template
或来源标签，不是运行时身份，也不是授权边界。

术语定义见 [authorization-glossary.md](authorization-glossary.md)。完整权限树见 [permission-tree.md](permission-tree.md)。

## 系统作用域命名

`team.system`、`team.template` 和普通 `team.<team_id>` 都是同一套 Team 资源模型。前两者只是保留 Team ID：system Team
承载系统工作区，Template Team 承载新 Team 的复制来源。

普通 Team 的 `team_id` 由 Team 名称派生（小写、非字母数字折叠为连字符，同名冲突追加 `-2`、`-3`），
批准申请时确定并保持不变。节点按 `.` 分段，因此 `team_id` 不含点。

能复用 Team 结构的资源必须继续使用 `team.<team_id>.*`：

```text
team.system.users.read
team.system.permission_templates.read
team.system.permission_assignments.manage
team.system.packages.grant
team.system.keys.distribute
```

`system.*` 只用于无法表达为 Team 资源的平台能力，例如启动、全局健康、运维、Schema 管理、存储配置和跨 Team 编排。

## 资源与权限

资源是标准来源。业务模块注册具体资源和动作，权限节点由资源树生成：

```python
resources.register("team.alpha.packages").add_perm("read", read).add_perm("manage", manage)
```

生成器从空树开始，仅为模块作用域内注册的资源分支自动补 `.grant`；
再加入模块声明的业务动作，例如 `.manage`、`.download`、`.distribute`。

`.grant` 只表示允许在系统内分发同一资源的权限，不参与资源自身业务判断。

## Permission Assignment

有效权限只来自活跃 Permission Assignment：

1. 直接分配；
2. Key Redemption 产生的分配；
3. 引用有效 Permission Template 的分配。

每条分配包含 node、effect、priority、grant effect、source、有效期和撤销状态。deny 在同优先级优先。`X-Team-Id` 只表示请求上下文，不产生权限。

Permission Catalog 定义系统里存在的节点；Permission Assignment 把节点绑定给目标用户并附加决策字段。

## 列表与工作区

系统里的列表、详情、字段、页面和操作都由有效 Permission Assignment 推导。服务器先读取真实资源表，再按请求者的有效 `read`
权限过滤行和字段。前端只保存完整 Permission Assignment 快照，并从这份快照推导导航和控件。

simple 模式下，首个有效用户自动成为服务器 Owner，并创建 `default` Team
作为初始工作区；后续访问来自模板、Key 兑换或显式分配。

用户列表由 `team.system.users.read` 和 `team.<team_id>.users.read` 控制。Team 用户列表同样从有效 Permission Assignment
推导。列表行先显示 GitHub 基础身份和系统公开字段；更详细字段需要更高权限。

Team 目录由 `system.teams.read/manage/grant` 控制。单个 Team 的详情、状态和设置由 `team.<team_id>.read/manage/grant`
控制。工作区切换只是上下文切换，不创建权限。

## Permission Template 范围

Permission Template 按 Team 独立存储：

```text
team.<team_id>.permission_templates.*
```

系统初始化时创建 system Team 和固定 Template Team。创建新 Team 时复制 Template Team 当前快照，并把其中的 Team 作用域重写到新
Team。

## 复用与扩展原则

- `Team` 是唯一的工作区和资源抽象；system 与固定 Template Team 是带有特殊策略
  元数据的保留实例。
- Permission Template、Permission Assignment、评估器、列表投影和 mutation
  工作流在所有 Team 间复用。
- 新模块只通过资源声明、动作、事件、命令、查询和端口扩展，不引入第二套授权
  存储、身份模型或特殊 allowlist。
- 前端使用共享的 Users、Assignment editor、Template editor、filter、table、
  preview、confirmation 和 audit 组件；上下文改变数据范围，不改变组件语义。

## 权限评估细则

权限节点使用规范化的点分隔格式。`*` 匹配所有节点，末尾 `.*` 匹配后代；
中间 wildcard 不合法。公开 API 的 `domain:resource.action` 在边界转换为
canonical node。

`manage` 和其他业务动作要求同一资源的 `.read` 有效。

Permission Template 派生权限会随着模板启用状态实时生效；Key 是快照，不因模板
后续变化而改变。模板禁用移除继承访问，但不影响直接 Assignment。

## 列表、字段与对象可见性

服务器先读取权威资源表，再按请求者有效的 `read` 过滤行、字段和详情。可见性
适用于 Users、Team 用户、Teams、Templates、Assignments、Keys、Packages、
Applications、Audit 和 Operations。前端快照只能投影菜单和控件，不能替代后端
过滤。

`read` 同时控制集合发现、对象详情和返回字段；`manage`、`download`、`revoke`
等动作不会扩大可见范围。单个对象的具体 read Assignment 可以允许已知对象详情，
但不能扩大集合枚举。

用户列表先显示 GitHub 基础身份和系统公开字段；敏感管理字段需要对应详情权限。
Owner、template、inherited 等 provenance 只用于展示和审计，不改变授权。

Team 出现在工作区选择器中，必须来自有效 Team-scoped visibility 或 System
Team-read 权限。Team 用户同样由有效 Assignment 和模板投影推导，绝不以
`team_members` 作为授权或可见性来源。

## Permission Assignment 资源与委托

Assignment 记录使用：

```text
team.<team_id>.permission_assignments.read
team.<team_id>.permission_assignments.manage
team.<team_id>.permission_assignments.grant
```

`manage` 负责创建、编辑、暂停、恢复、撤销和延长有效期；`.grant` 只负责向其他
用户分发 Assignment 资源权限，不负责写入 Assignment。直接创建 Assignment 不
要求先创建 Template，但必须满足 Catalog、当前 Team `manage`、目标资源 `.grant`
和逐节点 grantability 检查。

分发者不能授予高于自身有效 Assignment 的 priority，也不能通过 grant effect 为
deny 的 Assignment 继续委托。Owner、当前用户、最后必要 Owner、受保护资源等
保护条件由后端策略决定，前端只显示结果。

## 统一 mutation 流程

会减少权限、改变 Team 状态、修改模板或 Assignment、发行 Key、发布/禁用
Package、审批申请的操作统一遵循：

```text
preview -> confirmation token -> Idempotency-Key mutation -> refresh context,
resource list, authorization snapshot, and audit
```

重复使用相同 `Idempotency-Key` 返回原响应；相同 key 对不同请求必须失败。失败或
取消确认不得产生部分 Assignment、消耗 Key 或创建发布者 Grant。

## Package 与 Key 确认契约

Package v1 的动作集合固定为 `read`、`manage`、`download`。`manage` 同时覆盖
发布不可变 version、metadata、status 和 configuration；不存在独立 `publish`
节点。collection action 可作用于该 Team 的具体 Package，具体 Package 节点可
用于更窄的 allow/deny。

任意有效 Package action 可读取基本 metadata，但 archive 仍要求 `download`。
基本 metadata 包括 Package ID、名称、可见版本、状态、发布时间、简述、发布者
摘要和生成的权限范围，不包括 download token、archive URL、archive bytes、草稿
和其他用户授权详情。状态为 `published`、`disabled` 或 `unpublished`；禁用/
取消发布不改写既有 Assignment。

Package version 一个版本只能有一个不可变 archive。preview 发现已存在版本时
必须阻止确认和上传，即使 digest 相同也不能视为成功。发布者的自动 Grant 必须在
发布成功后创建，不能授权创建它自己的发布操作。

Key issuance 使用统一的 node、priority、effect、grant effect 和 validity editor。
一个 Key 可包含多个独立 Assignment；明文随库存保存，持有 `keys.read` 的账户可以
再次读取，但明文不写入日志。

## 页面职责与验收场景

System Workspace 的 Teams、Applications、Keys、Packages、Audit、Operations
分别承担跨 Team 目录、申请审批、Key 检查、package catalog、审计和运行状态。
普通 Team 的 Keys、Packages、Audit 只作用于当前 Team。Template Team 和普通
Team 不显示 Teams、Applications、Operations；System Packages 只读，不能上传或
发行。Overview 在 System 是跨 Team 摘要，在 Team 是当前 Team 摘要。

必须覆盖这些边界场景：已有 version 的发布 preview 被阻断；只有 `manage` 无
`download` 不能下载 archive；Assignment 被撤销、过期或 deny 后对应页面/动作
消失；模板禁用立即移除继承权限但保留直接权限和审计记录；撤销 Team 用户可见性
Assignment 后该用户从派生列表消失；切换到无页面权限的上下文回到该上下文
Overview。
