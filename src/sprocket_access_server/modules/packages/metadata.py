from __future__ import annotations

import re
from typing import Any

# 这些字段的形状错了，客户端会拒整份索引或直接抛异常，因此在写入时就挡掉。
_CATEGORY = frozenset({"gameplay", "utility", "library", "visual", "audio", "translation", "other"})
_ENTRY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$")
_PACKAGE_ID = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)+$")
# 安装规则：type 是 `<加载器>:<类别>`（或 `<加载器>:*`），target 是 `{Sprocket}/...`。
_FILE_TYPE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*:(?:[a-z0-9]+(?:[.-][a-z0-9]+)*|\*)$")
_CONCRETE_FILE_TYPE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*:[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_SUPPLY_TARGET = re.compile(r"^\{Sprocket\}(?:/[A-Za-z0-9._-]+)*$")
_SUBPATH = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
# 仓库是 GitHub 的 `<owner>/<name>`；未知时不发这个键。
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# 公开 v3 的标签是短横线小写标识。
_TAG = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_string_list(value: Any, field: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must be a list of non-empty strings")


def _require_localized(value: Any, field: str) -> None:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{field} must be a non-empty object of language -> text")
    for key, text in value.items():
        _require_string(key, f"{field} key")
        _require_string(text, f"{field}.{key}")


def validate_metadata(metadata: dict[str, Any]) -> None:
    """校验包元数据里客户端会强依赖的部分；不合规直接拒绝写入。"""
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    if "name" in metadata and not _ENTRY_NAME.fullmatch(str(metadata["name"]).strip()):
        raise ValueError("metadata.name must match the registry name pattern")
    if "license" in metadata:
        if len(_require_string(metadata["license"], "metadata.license")) > 80:
            raise ValueError("metadata.license must be at most 80 characters")
    if "authors" in metadata:
        authors = _as_list(metadata["authors"], "metadata.authors")
        if not authors:
            raise ValueError("metadata.authors must not be empty")
        for item in authors:
            if len(_require_string(item, "metadata.authors[]")) > 80:
                raise ValueError("metadata.authors[] must be at most 80 characters")
        if len({str(item) for item in authors}) != len(authors):
            raise ValueError("metadata.authors must not repeat an entry")
    if "tags" in metadata:
        tags = _as_list(metadata["tags"], "metadata.tags")
        for item in tags:
            if not _TAG.fullmatch(_require_string(item, "metadata.tags[]")):
                raise ValueError("metadata.tags[] must match the registry tag pattern")
        if len({str(item) for item in tags}) != len(tags):
            raise ValueError("metadata.tags must not repeat an entry")
    if "recommendations" in metadata:
        recommendations = _as_list(metadata["recommendations"], "metadata.recommendations")
        for item in recommendations:
            if not _PACKAGE_ID.fullmatch(item):
                raise ValueError("metadata.recommendations entries must be package ids")
        if len({str(item) for item in recommendations}) != len(recommendations):
            raise ValueError("metadata.recommendations must not repeat an entry")
    if "category" in metadata and str(metadata["category"]).strip() not in _CATEGORY:
        raise ValueError(f"metadata.category must be one of {sorted(_CATEGORY)}")
    if "dependencies" in metadata:
        # 客户端按对象读取依赖（`item.get("id")`），字符串数组会直接抛异常。
        for item in _as_list(metadata["dependencies"], "metadata.dependencies"):
            if not isinstance(item, dict) or {"id", "version", "when"} - set(item):
                raise ValueError("metadata.dependencies entries must be {id, version, when} objects")
            if not _PACKAGE_ID.fullmatch(str(item["id"])):
                raise ValueError("metadata.dependencies[].id must be a package id")
            _require_string(item["version"], "metadata.dependencies[].version")
            _require_string(item["when"], "metadata.dependencies[].when")
    if "display_name" in metadata:
        _require_localized(metadata["display_name"], "metadata.display_name")
    if "description" in metadata:
        _require_localized(metadata["description"], "metadata.description")
    # 规则是落点的唯一来源，也是公开 v3 的必填字段：包必须声明至少一条规则。
    install = metadata.get("install")
    if not isinstance(install, dict):
        raise ValueError("metadata.install must declare files or payload")
    if "scan_dlls" in install and not isinstance(install["scan_dlls"], bool):
        raise ValueError("metadata.install.scan_dlls must be a boolean")
    if "exclude" in install:
        _require_string_list(install["exclude"], "metadata.install.exclude")
    if "mode" in install and str(install["mode"]) not in {"standard", "patch"}:
        raise ValueError("metadata.install.mode must be standard or patch")
    _validate_install_rules(install)
    for key in ("release", "featured", "required_permission"):
        if key in metadata:
            # 这些键属于公开目录的语义，私有条目出现即被客户端整份拒绝。
            raise ValueError(f"metadata.{key} is not allowed in a private package")
    if "repository" in metadata and metadata["repository"] is not None:
        if not _REPOSITORY.fullmatch(str(metadata["repository"]).strip()):
            raise ValueError("metadata.repository must be <owner>/<name>")


def _validate_install_rules(install: dict[str, Any]) -> None:
    """校验 v2 安装规则：形状、枚举、正则与互斥约束，对齐客户端 schema。"""
    files = install.get("files")
    payload = install.get("payload")
    if files is not None and payload is not None:
        raise ValueError("metadata.install must not declare files and payload together")
    if not files and not payload:
        # 规则是落点的唯一来源：声明了 install 就必须至少给出一条规则，否则条目发出去也没人能用。
        raise ValueError("metadata.install must declare files or payload")
    if files is not None:
        for rule in _as_list(files, "metadata.install.files"):
            _require_rule(rule, "metadata.install.files", required=("match", "type"))
            if not _FILE_TYPE.fullmatch(str(rule["type"])):
                raise ValueError("metadata.install.files[].type must be <loader>:<kind> or <loader>:*")
            _require_optional_rule_fields(rule, "metadata.install.files")
        if not isinstance(install.get("scan_dlls"), bool):
            raise ValueError("metadata.install.scan_dlls is required with files")
        if "exclude" not in install:
            raise ValueError("metadata.install.exclude is required with files")
    if payload is not None:
        for rule in _as_list(payload, "metadata.install.payload"):
            _require_rule(rule, "metadata.install.payload", required=("match", "target"))
            if not _SUPPLY_TARGET.fullmatch(str(rule["target"])):
                raise ValueError("metadata.install.payload[].target must start with {Sprocket}")
            _require_optional_rule_fields(rule, "metadata.install.payload")
        if "exclude" not in install:
            raise ValueError("metadata.install.exclude is required with payload")
        if "scan_dlls" in install:
            raise ValueError("metadata.install.scan_dlls must not be set with payload")
    if "replace" in install:
        # 整目录接管的声明：必须显式标记为 patch。
        types = _as_list(install["replace"], "metadata.install.replace")
        if not types:
            raise ValueError("metadata.install.replace must not be empty")
        if len({str(item) for item in types}) != len(types):
            raise ValueError("metadata.install.replace must not repeat a type")
        if str(install.get("mode", "")) != "patch":
            raise ValueError("metadata.install.replace requires mode 'patch'")
        for item in types:
            if not _CONCRETE_FILE_TYPE.fullmatch(str(item)):
                raise ValueError("metadata.install.replace entries must be concrete types")


def _require_rule(rule: Any, field: str, *, required: tuple[str, ...]) -> None:
    if not isinstance(rule, dict) or any(key not in rule for key in required):
        raise ValueError(f"{field} entries must be objects with {', '.join(required)}")
    for key in required:
        _require_string(rule[key], f"{field}[].{key}")


def _require_optional_rule_fields(rule: dict[str, Any], field: str) -> None:
    if "subpath" in rule and not _SUBPATH.fullmatch(str(rule["subpath"])):
        raise ValueError(f"{field}[].subpath must be a relative directory")
    if "layout" in rule and str(rule["layout"]) not in {"file", "tree"}:
        raise ValueError(f"{field}[].layout must be file or tree")


def _as_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value
