# 后端模块架构

本文档描述当前后端模块架构。

## 目标目录

```text
src/sprocket_access_server/
  core/             核心契约、事件、模块注册、运行时
  infrastructure/   配置、日志、数据库、网络、存储、安全、工具
  modules/          业务模块与注册
  presentation/     ASGI/HTTP/WS 展示层
  domain/           领域模型与协议类型
```

`core.runtime` 负责启动、组装和关闭。`infrastructure` 提供通用运行时能力；`modules` 只负责注册业务能力，不直接创建基础设施。

## 依赖规则

业务模块可以依赖 `core` 契约和 `infrastructure` 暴露的框架端口，但不能直接：

- 操作 SQLite 连接；
- 手写 HTTP 路由；
- 自己开 socket；
- 读取进程环境；
- 写文件；
- 配置日志；
- 导入别的模块的具体实现。

允许方向：

```text
modules/* -> core/*
modules/* -> infrastructure ports
infrastructure/* -> core/*
core.runtime -> infrastructure + modules/*
```

禁止方向：

```text
modules/* -> sqlite3 / raw DB
modules/* -> ASGI internals / raw network
modules/* -> os.environ / global config
modules/* -> sibling module implementation
core/*    -> infrastructure/*
```

## infrastructure 的职责

`infrastructure` 是完整的应用框架，负责：

- 统一配置；
- 统一结构化日志；
- 数据库连接、事务、迁移、仓储适配；
- HTTP/WS 启动、请求解析、路由挂载、中间件、封包、错误映射；
- 会话/认证适配；
- 对象存储、签名、幂等、确认、脱敏；
- 事件总线、命令/查询派发、监听器注册；
- 资源树聚合、WS 操作分发、权限生成与校验。

业务模块通过显式端口或注册上下文获得这些能力，不自己创建基础设施。

## 模块职责

每个业务模块只负责自己的领域行为，并通过框架注册：

- 资源分支和额外业务动作；
- WS 操作；
- schema/migration 声明；
- 仓储适配；
- 事件监听与事件发布；
- 命令、查询、用例。

模块之间通过事件、命令、查询或端口通信，不直接引用对方私有类或表。

## 资源与权限生成

资源树在组装时从空树开始。每个模块注册资源分支和额外动作，资源是后端规范词表。权限节点与 WS 操作都从同一个资源节点派生。

推荐注册方式：

```python
resources.register(f"team.{team_id}.packages")
    .add_perm("read", read)
    .add_perm("download", download)
```

`add_perm(action, handler)` 会同时注册一个 WS 操作，并把 `<resource>.<action>` 加进权限目录。角括号只用于文档模板，运行时必须使用具体节点。

注册器会：

1. 收集所有资源声明；
2. 仅给模块作用域内注册的资源分支自动补 `.grant`；
3. 加入模块注册的额外动作；
4. 校验模块自动生成的分支都有 `.grant`，并保留资源显式注册的其他动作；
5. 发布生成后的权限目录；
6. 用 `(action, node)` 分发 WS 包。

`.grant` 只表示“可以在系统内分发该资源的权限”，不能当作业务动作。

## 模块通信

模块通过低耦合机制通信：

- 事件：已经发生的事实；
- 命令：请求另一个模块负责的变更；
- 查询/读模型：跨模块读取；
- 端口：需要持久化或框架能力时使用。

## 验收标准

- `infrastructure` 只承载通用框架能力，不承载业务逻辑。
- 没有模块直接使用 raw DB / network / config / logging 原语。
- 权限目录由模块注册生成。
- 每个模块注册的资源分支都有 `.grant`；模块外手动注册的资源不自动补权限。
- 跨模块行为通过事件、命令、查询或端口。
- 配置、日志、数据库、网络、存储、安全、审计、幂等、确认都由统一基础设施提供。
- 服务启动和关闭由 `core.runtime` 负责，只有一条组装路径。

## 当前布局状态

```text
core/                          contracts, ports, runtime
infrastructure/
  configuration/
  database/
  events/
  logging/
  messaging/
  security/
  storage/
  utilities/
modules/
  system/
  permission_assignments/
  permission_templates/
  packages/
  keys/
  audit/
  team/
  users/
  applications/
```

模块层行为通过资源注册器和
`presentation/asgi.py` 的 WS 分发器暴露。

`infrastructure/messaging/` 提供 SQLite 邮件 outbox：业务事务成功后
`enqueue`，ASGI lifespan worker 以 `deliver_once` 投递，失败按指数退避
重试（`2^attempts`，上限 3600s），`enqueue/failed/sent` 均写审计。
