# 运行通信契约

本文件记录浏览器前端、GitHub OAuth 回调、会话 token、WebSocket 和 Team context 的运行边界。它是前端实现和后端验收的当前来源。

## 同端口提供前端与后端

一个 ASGI 进程即可同时提供两侧：`/v1` 与 `/ws` 给桌面客户端，`/admin`（含 SPA 深链与 `/admin/assets`）给管理端，`/healthz` 供探活，根路径 `307` 到 `/admin/`。镜像在构建阶段把管理端编成静态产物（`admin-ui/dist`），运行阶段只需要 `src` 与这份产物。

前端与后端同源时不需要额外配置：未设 `VITE_API_ORIGIN` 时前端用 `window.location.origin`，WebSocket 也走同源 `/ws`；`SMAS_FRONTEND_ORIGIN` / `SMAS_WS_ALLOWED_ORIGINS` 留空即可（WebSocket 来源校验会自动接受请求自身的 host）。只有前端由别的 origin 提供时，才按下一节填写实际 origin。

## Origin 配置

`SMAS_FRONTEND_ORIGIN` 和 `SMAS_WS_ALLOWED_ORIGINS` 都填写浏览器实际打开前端页面的 origin，不填写后端监听地址。

本地 Vite 默认值：

```env
SMAS_FRONTEND_ORIGIN=http://localhost:5173,http://127.0.0.1:5173
SMAS_WS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

`localhost` 和 `127.0.0.1` 是不同 origin。前端从哪个地址打开，就必须包含哪个地址。后端 `http://127.0.0.1:8787` 只属于 `SMAS_HOST`、`SMAS_PORT` 和 `SMAS_GITHUB_CALLBACK_URL`，不作为默认前端 origin。

## GitHub Web 回调

前端开始登录前先读取 `system.server_info`，并使用：

```json
{"identity_policy": {"auth_methods": {"github": {"web": true, "device": true, "token_exchange": true}}}}
```

`web=true` 表示后端同时配置了 `SMAS_GITHUB_CLIENT_ID`、`SMAS_GITHUB_CLIENT_SECRET` 和 `SMAS_GITHUB_CALLBACK_URL`。`device=true` 表示后端配置了 `SMAS_GITHUB_CLIENT_ID` 且 provider 支持 Device flow。前端只有在 `web=true` 时才启动 Web popup；否则应 fallback 到 Device flow。

前端通过 `system.authentication.github.web` 的 `start` 动作取得 GitHub 授权 URL，然后打开弹窗。

GitHub 回调到：

```text
/v1/auth/github/callback
```

后端完成 code/state 校验和 GitHub code exchange 后，返回 HTML，并对 `SMAS_FRONTEND_ORIGIN` 中的每个 origin 执行：

```js
window.opener?.postMessage(message, origin)
```

成功消息：

```json
{"ok": true, "payload": {"token": "...", "github_user_id": "...", "created_at": 0, "expires_at": 0}}
```

失败消息：

```json
{"ok": false, "error": {"code": "...", "message": "..."}}
```

回调页必须在成功和失败时都发送消息，避免前端只看到超时。

## Session Token

服务器 session token 来自：

- `/v1/auth/github/exchange`；
- `system.authentication.github.web.start` 后的 callback `postMessage`；
- `system.authentication.github.device.poll` 完成结果。

Device flow 使用：

```text
system.authentication.github.device.start
system.authentication.github.device.poll
```

`start` 返回 `flow_id`、`user_code`、`verification_uri`、`interval` 和 `expires_in`。前端显示 `user_code` 和验证链接，并按 `interval` 轮询。`poll` 返回 `{"status":"pending"}` 时继续等待，返回 session payload 时按普通登录成功处理。

客户端持久化 token 后，所有受保护 HTTP 和 WebSocket 操作都使用：

```http
Authorization: Bearer <session-token>
```

浏览器 WebSocket 无法可靠设置自定义 header 时，可以使用：

```text
/ws?access_token=<session-token>
```

后端只把 query token 转换为本次连接内部的 Authorization header；日志必须脱敏 `access_token`。

## WebSocket Envelope

请求格式：

```json
{"action": "read", "node": "system.authentication.me", "data": {}, "request_id": "client-id"}
```

响应格式：

```json
{"ok": true, "action": "read", "node": "system.authentication.me", "data": {}, "request_id": "client-id"}
```

失败响应：

```json
{"ok": false, "action": "read", "node": "...", "request_id": "client-id", "error": {"code": "...", "message": "..."}}
```

浏览器 WebSocket 客户端需要发送变更元数据时，可以在 envelope 中加入字符串
`headers` 映射。后端只接受 `Idempotency-Key`、`X-Confirmation-Token` 和
`X-Team-Id`；它们不能覆盖连接认证。header 名不区分大小写：服务端按小写归一化，
同名不同大小写的旧值会被替换而不是并存。连接上的 `x-team-id` 只是默认作用域，
`X-Team-Id` 是逐请求作用域，因此同一条连接上的并发操作不必靠 `select` 的顺序。

```json
{"action": "manage", "node": "team.<team_id>.permission_assignments", "headers": {"Idempotency-Key": "client-mutation-id"}, "data": {}}
```

客户端重连后必须重新发送带 session token 的连接，重新读取 `system.authentication.me`，
并显式 `select` 一次 Team 作用域（或在每个请求的 envelope headers 里带 `X-Team-Id`）。
旧连接上的 Team context 不跨连接保留。

广播是**尽力而为**：帧里只有 `kind`/`action`/`node`/`data`，没有序号或时间戳，也没有补发或重放。断线期间发生的变化不会补投，订阅方据"重新读取 + 周期性对账"恢复一致。

事件可见性只看事件自身与当前授权：带 `team_id` 的事件按该 Team 判定，因此一条连接可以跨多个 Team 收事件；不带 `team_id` 的系统作用域事件固定按 `system` 判定，不跟随连接最近一次选中的 Team。授权判定抛错时按不可见处理并记 warning 日志。

## Team Context

Team context 只是请求上下文，不产生权限。

WebSocket 连接内切换 Team：

```json
{"action": "select", "node": "system.authentication.team_context", "data": {"team_id": "team-id"}, "request_id": "..."}
```

后端会校验当前用户是该 Team 成员。成功后，本 WebSocket 连接后续请求使用选中的 `x-team-id`。失败时前端必须撤回本地选择，保留最后一个成功的工作区。

HTTP 请求使用：

```http
X-Team-Id: <team-id>
```

工作区列表只能来自 `system.authentication.me.teams`。前端默认值必须选择后端返回的工作区或仍然有效的持久化工作区，不能创建空工作区或硬编码工作区。

## 服务身份

外部服务（如 Discord 机器人）以**服务号**接入，走与真人相同的授权与读取面。

**凭据**：环境变量 `SMAS_SERVICE_ACCOUNTS` 是 JSON 对象，键为服务号 id、值为该服务号的密钥：

```env
SMAS_SERVICE_ACCOUNTS={"discord-bot":"<secret>"}
```

服务号 id 必须是**非数字**字符串。真人身份是 GitHub 数字 id，保留 id 取非数字即不可能被真实登录占用；配置里出现纯数字键直接拒绝。

**主体**：服务号是 `users` 表里的一条保留 id 行（与内置 `system` 行同类）。它没有专用权限节点，也不享受任何旁路：管理员用现有节点与权限模板给它授权，`create_grant` 只要求该行 `status='active'`。

**兑换会话**：

```http
POST /v1/auth/service/exchange
Content-Type: application/json

{"service_id": "discord-bot", "secret": "<secret>"}
```

成功返回与 GitHub 兑换相同形状的会话（`token`、`expires_at`、以及该服务号的用户信息）。等价的 WebSocket 动作是 `system.authentication.service.exchange`。密钥比较使用定时安全比较。

错误：未知 `service_id` 与错误 `secret` 返回**同一个** `401 service_credential_rejected`，避免枚举服务号。

**读取**：服务号用现有入口读取，权限按它被授予的节点判定，没有服务专用接口。权限目录是 `system.permissions` 的 `read`；资源变更事件按 `${node}.read` 投递给订阅方，事件载荷不超过该读权限可见的范围。

**审计**：兑换成功写一条 `service.exchange`，actor 为 `service:<service_id>`；失败只记 warning 日志，不写审计行，避免凭据猜测刷审计表。用服务号会话执行的动作同样归到该 actor，不以用户身份出现。

## 授权检索

`team.<team_id>.permission_assignments` 的 `read` 支持这些过滤，全部可选、可组合：

| 参数 | 匹配方式 | 用途 |
| --- | --- | --- |
| `user_id` | **等值** `github_user_id = ?` | 按 id 精确定位某人；不会命中前缀相同的其它 id |
| `user_id_like` | 子串 | 搜索框 |
| `node` | **等值** | 按完整节点名定位 |
| `node_like` | 子串 | 搜索框 |
| `source` | 子串，匹配 `source_type:source_id` | 按来源筛选 |
| `status` | 枚举 `active`/`revoked`/`suspended`/`expired` | |
| `expiry` | 枚举 `expired`/`permanent` | |

每行自带 `github_user_id`、`team_id`、`node`、`effect`、`source_type`、`source_id`（模板名）、`status`、`expires_at`，因此按 Team 归并授权不需要解析节点前缀。响应同时给出 `catalog`（当前节点目录）、`limit`、`offset`、`total`。

## 激活码检索与发放

`team.<team_id>.keys` 的 `read` 支持 `status`（`unused`/`redeemed`/`revoked`）、`prefix`（前缀匹配）、`batch_id`（等值）、`delivered`（`true`=已发放、`false`=未发放）、`limit`、`offset`。每行给出 `key_id`、`batch_id`、`key_prefix`、`plaintext`、`status`、`note`、`permissions`、`expires_at`、`redeemed_github_user_id`、`redeemed_at`、`delivered_to`、`delivered_at`、`team_id`；响应另带按状态计数的 `counts` 与 `total`。

按批次取还没用掉的码：`{"status": "unused", "batch_id": "<batch_id>", "limit": N}`。`read_batch` 给出单批的统计（数量与各状态计数），逐枚明细走上面的 `read`。

发放把"这枚码交给谁"记在服务端，与兑换（`redeemed_*`）是两件事：

```json
{"action": "take", "node": "team.<team_id>.keys", "data": {"batch_id": "<batch_id>", "count": 2, "recipient": "discord:42"}}
```

`take` 在一个事务里取走 N 枚未使用且未发放的码并记下收件人，返回 `{keys: [...], count: N}`（含明文）；可用数量不足时返回 `409 key_unavailable`，并发取用不会发出同一枚。`release` 撤销一枚**尚未兑换**的发放记录：`data: {"key_id": "..."}` → `{"key_id": "...", "delivered_to": null}`。两个动作分别需要 `keys.take` / `keys.release`，并各写一条审计（`keys.take` / `keys.release`）。

## 错误码契约

失败响应统一携带 `error.code`。客户端按稳定代码分支、用 i18n 映射用户可见
文案，不直接展示后端原文。代码表由 `domain/errors.py` 的 `ERROR_CATALOG`
执行：每个码绑定固定 HTTP 状态，`ApiError` 构造时校验一致。

| code | status | 含义 |
| --- | --- | --- |
| `permission_denied` | 403 | 上下文或资源权限不足 |
| `service_credential_rejected` | 401 | 服务号的密钥或服务号 id 无效 |
| `system_denied` | 403 | 缺少系统级权限 |
| `team_permission_denied` | 403 | 缺少 Team 资源权限 |
| `team_access_denied` | 403 | Team 不可用（非 active） |
| `team_not_found` | 404 | Team 不存在 |
| `team_application_exists` | 409 | 已有同名待审 Team 申请 |
| `reserved_team` | 403 | 保留 Team 的状态不可变更 |
| `self_lockout` | 403 | 不能变更自己账户的状态 |
| `system_account` | 403 | 内置 system 账户的状态不可变更 |
| `team_context_required` | 400 | 需要显式 Team 上下文 |
| `content_denied` | 403 | 内容级访问被拒 |
| `admin_forbidden` | 403 | 管理能力被拒 |
| `invalid_session` | 401 | session 无效或过期 |
| `invalid_request` | 400 | 请求数据无效 |
| `invalid_idempotency_key` | 400 | 缺少或非法幂等键 |
| `idempotency_key_reused` | 409 | 同键不同请求 |
| `confirmation_required` | 400 | 需要确认令牌 |
| `not_found` | 404 | 资源不存在 |
| `grant_not_found` | 404 | 授权记录不存在 |
| `template_not_found` | 404 | 权限模板不存在 |
| `package_not_found` | 404 | Package 版本不存在 |
| `key_not_found` | 404 | Key 不存在 |
| `key_batch_not_found` | 404 | Key 批次不存在 |
| `key_unavailable` | 409 | Key 不可使用 |
| `key_update_rejected` | 409 | Key 状态更新非法 |
| `grant_scope_denied` | 403 | 超出可分发范围 |
| `github_oauth_invalid` | 400 | GitHub 授权拒绝或过期 |
| `github_oauth_unavailable` | 503 | GitHub 登录服务不可用 |
| `github_device_flow_expired` | 410 | 设备授权过期 |
| `github_device_flow_invalid` | 502 | 设备授权结果无效 |
| `github_device_flow_unavailable` | 503 | 设备登录服务不可用 |
| `github_token_rejected` | 401 | GitHub 判定令牌无效 |
| `github_token_forbidden` | 403 | GitHub 403（权限不足或限流），令牌可能仍有效 |
| `github_identity_failed` | 502 | GitHub 身份失败 |
| `github_identity_invalid` | 502 | GitHub 身份无效 |
| `github_identity_unavailable` | 503 | 身份服务不可用 |
| `invalid_github_token` | 400 | GitHub 令牌无效 |
| `invitation_invalid` | 400 | 邀请无效、已过期或不属于当前账户 |
| `authorization_unavailable` | 503 | 授权服务不可用 |
| `admin_unavailable` | 503 | 管理服务不可用 |
| `admin_ui_unavailable` | 503 | 管理 UI 服务不可用 |
| `distribution_unavailable` | 503 | 分发服务不可用 |
| `signing_unavailable` | 503 | 签名服务不可用 |
| `registration_disabled` | 403 | 注册被禁用 |
| `registration_unavailable` | 503 | 注册服务不可用 |
| `rate_limit_exceeded` | 429 | 触发限流 |

## Package Upload

上传使用 Team 作用域资源：

```text
team.<team_id>.package_uploads.create
team.<team_id>.package_uploads.confirm
```

创建上传草稿请求只提交 Package 身份、版本、大小、内容类型和可选 metadata：

```json
{"package_id": "example.mod", "version": "1.0.0", "size": 123, "content_type": "application/zip", "metadata": {}}
```

客户端不能提交 `permission_node` 或 `required_permission`。后端按 `team_id` 和 `package_id` 生成 `permission_node`，并在响应中返回给前端展示：

```json
{"upload_id": "...", "package_id": "example.mod", "version": "1.0.0", "permission_node": "team.<team_id>.example_mod", "upload": {}, "expires_at": 0, "created_by": "..."}
```

`permission_node` 由 `package_resource_node(team_id, package_id)` 生成：`team.<team_id>.<package_id>`，其中 `.` 替换为 `_`（如 `example.mod` → `example_mod`），不带 `packages` 段。

确认上传请求：

```json
{"upload_id": "...", "sha256": "64-hex-digest"}
```

落点由包声明的安装规则决定，因此确认请求不带 `target`。后端按载荷开头是不是 `PK` 判断形态：裸 DLL 的资产名用 `.dll`，归档始终是 `.zip`，即使里面只装了一个 DLL。

### 安装规则

安装规则属于**包**（存在包实体的 metadata 里），版本只承载上传的文件与版本号。规则是公开 v2 的形状：

```json
{"install": {"mode": "standard", "files": [{"match": "*.dll", "type": "bepinex:plugin", "subpath": "MyMod"}],
             "payload": [{"match": "**", "target": "{Sprocket}", "layout": "tree"}],
             "replace": ["bepinex:core"], "scan_dlls": true, "exclude": ["Mods/README.md"]}}
```

- `files`（typed 规则）与 `payload`（加载器自己的载荷）互斥；`payload` 不得与 `scan_dlls` 同时出现；`replace` 只在 `mode:"patch"` 下出现。
- `type` 由客户端按**本机已装的加载器**解析成目录，服务端把它当不透明字符串传递。
- 包必须声明规则：既没有 `files` 也没有 `payload` 时写入即被拒（`metadata.install must declare files or payload`），
  空规则表（`files: []`）同样拒绝 —— 条目里的 `install` 因此总是带着规则，与公开 v3 的必填项一致。
- 元数据的其余字段按公开 v3 的约束校验：`authors` 非空、不重复、每项不超过 80 字符；`tags` 形如
  `[a-z0-9][a-z0-9-]{0,31}` 且不重复；`license` 不超过 80 字符；`recommendations` 是包 id 且不重复；
  `repository` 只收 `<owner>/<name>`（不合法或未知时条目里不出现这个键）。
- 发布时按规则校验归档条目：**未被规则或 `exclude` 覆盖的条目会被拒**（`archive entry has no install rule: <path>`）。单个 DLL 只需要规则存在，条目路径由客户端解析。
- 条目里不出现版本级文件清单：落点完全由规则驱动，服务端不再派生 `{name, target, sha256}`。

### 发布载荷的地址

条目里每个 release 的 `assets[].download_url` 是**绝对 URL**（`<scheme>://<host>/v1/packages/<id>/download`，不含查询串），由本次请求的 origin 拼出；没有请求上下文时用 `SMAS_PUBLIC_BASE_URL`。客户端自行追加 `?version=<v>`。

地址只允许两类：`https://` 任意主机（可带端口与 IPv6 字面量），或 `http://` 的 loopback（`localhost`/`127.0.0.1`/`[::1]`）；不得带凭据、查询串或片段。因此**公网部署必须走 https**：以明文 http 在非 loopback 主机上对外服务时，索引里的地址会被客户端拒绝，此时应把 `SMAS_PUBLIC_BASE_URL` 指向 https 入口。

条目里的地址始终是**服务端自己的下载端点**；下载端点再决定字节怎么出去：

- **本地存储**：200 流式返回（`Content-Length` = `assets[0].size`）。
- **对象存储**：302 到桶的短寿命签名 URL（`SMAS_DOWNLOAD_URL_TTL`，默认 300 秒）。目标 origin 必须在
  `/v1/server-info` 的 `download_origins` 里：桶的 origin 由存储 URL 自动推出，另有 CDN 等来源时写
  `SMAS_DOWNLOAD_ORIGINS`（逗号分隔，裸主机名只接受 https）。客户端据此校验来源后再去取字节。

确认成功后，后端发布 Package，并动态注册 `team.<team_id>.<package_id>` 下的 `read/manage/download/grant` 权限节点。
