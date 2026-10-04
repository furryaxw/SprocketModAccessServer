# Sprocket Mod Access Server

[中文](README.md) | **English**

Sprocket Mod Access Server is a self-hosted access and distribution service for Sprocket Mod Manager.
It handles GitHub authentication, sessions, permission trees, Team/system workspace isolation, Key issuance, permission assignment, package publication, audit, and frontend/WS resource dispatch.

## Layout

```text
src/sprocket_access_server/
  core/            core contracts, permission tree, events, runtime composition
  infrastructure/  config, logging, database, network, storage, security, adapters
  modules/         business modules and registrations
  presentation/    ASGI and HTTP/WS presentation
  domain/          domain models and protocol types
```

Production ASGI factory:

```text
sprocket_access_server.core.runtime:build_app
```

## Local run

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m src.sprocket_access_server.core.runtime
```

The entry point runs from the checkout root; `.env`, `data/` and `logs/` resolve from the checkout instead of the working directory, and `SMAS_ROOT` moves runtime state outside the checkout.

Or with Uvicorn:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\uvicorn.exe --factory sprocket_access_server.core.runtime:build_app --env-file .env --host 127.0.0.1 --port 8787
```

## License

This project is licensed under GNU Affero General Public License v3.0
(AGPL-3.0). See [LICENSE](LICENSE) for the canonical text.
