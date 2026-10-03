# Sprocket Mod Access Server

**中文** | [English](README.en.md)

Sprocket Mod Access Server 是一个面向 Sprocket Mod Manager 的自托管访问与分发服务。
它负责 GitHub 身份验证、会话、权限树、Team/系统工作区隔离、Key 发放、权限分配、包发布、审计，以及前端/WS 资源分发。

## 目录结构

```text
src/sprocket_access_server/
  core/            核心契约、权限树、事件、运行时组装
  infrastructure/  配置、日志、数据库、网络、存储、安全、通用适配器
  modules/         业务模块与注册
  presentation/    ASGI 与 HTTP/WS 展示层
  domain/          领域模型与协议类型
```

生产 ASGI 工厂：

```text
sprocket_access_server.core.runtime:build_app
```

## 本地运行

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m sprocket_access_server.core.runtime
```

或使用 Uvicorn：

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\uvicorn.exe --factory sprocket_access_server.core.runtime:build_app --host 127.0.0.1 --port 8787
```

## 相关文档

- [授权设计](docs/authorization-design.md)
- [权限词汇表](docs/authorization-glossary.md)
- [后端模块架构](docs/backend-module-architecture.md)
- [权限树](docs/permission-tree.md)

## License

本项目采用 GNU Affero General Public License v3.0（AGPL-3.0），详见 [LICENSE](LICENSE)。
