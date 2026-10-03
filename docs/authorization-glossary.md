# 授权词汇表

这份词汇表是 Access Server 的术语标准，用于 API 文档、前端文案和授权设计。

## 核心对象

### User

通过 GitHub 认证的人。User 不是运行时角色，也不是身份模板。

### Team

唯一的工作区和资源容器抽象。system Team、固定 Template Team 和普通 Team 都是 Team 实例。

### System Team

承载系统工作区的保留 Team。它和普通 Team 共享存储、Permission Template、Permission Assignment、评估器和 UI 组件。

### Template Team

系统初始化时创建的固定 Team。它不是独立资源类型。新 Team 会复制它的当前快照并重写 Team 作用域。

### Permission Template

一组可复用的权限分配。它不是运行时角色。每个 Team 独立保存自己的 Permission Template。

### Permission Assignment

权限系统真正评估的授权输入：一个权限节点，加上 effect、priority、grant effect、source、有效期和撤销字段。

### Grant

资源权限里的动作名，表示允许在系统内分发同一资源的权限。它不是授权记录的产品名。

### Key

一次性分发容器，携带已经校验过的 Permission Assignment。Key 发行使用 `distribute`。

## 权限术语

### Permission Node

规范化点号权限路径，例如：

```text
team.system.users.read
team.<team_id>.packages.read
team.<team_id>.<package_id>.download
```

中间通配符无效，结尾 `.*` 匹配后代。

### Permission Catalog

系统识别并允许校验的权限节点目录。目录由模块注册生成；模块作用域内的资源分支自动获得 `.grant`，
再加模块声明的业务动作。模块外手动注册的资源只包含显式注册的业务动作。

### Read

统一可见性权限，控制列表、详情、字段和页面区块。

### Manage

资源变更权限，依赖同一资源的 `read`。

### Grant

系统内分发同一资源权限的能力，不能替代业务操作权限。

### Distribute

Key 专用动作，只用于 Key 发行和分发。

### Source / Provenance

仅用于展示的来源信息，例如直接分配、Permission Template、Owner 继承或 Key Redemption。它不会扩大可见范围。

## 规则

### Permission Snapshot

前端只保存一份完整 `PermissionAssignment[]` 快照。导航、页面区块、行、字段和控件都从这份快照推导。

### Team Context

选中的 Team 和 `X-Team-Id` 只表示请求上下文，不创建权限或可见性。

### Permission Prerequisite

编辑器选择 `manage` 或其他额外动作时自动勾选 `read`。前后端只保存独立节点；如果 `read` 被 deny、过期或撤销，依赖动作自动失效。
