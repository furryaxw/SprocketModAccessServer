from __future__ import annotations

import fnmatch
import zipfile
from typing import Any, BinaryIO

# 载荷形态：`zip` 是归档，`dll` 是单个 DLL。资产名按它给扩展名。
PAYLOAD_ZIP = "zip"
PAYLOAD_DLL = "dll"
# 版本元数据里记录载荷形态的保留键：只用于重建资产名，不进入条目输出。
PAYLOAD_KIND_KEY = "payload_kind"

_ZIP_MAGIC = b"PK"
_RULES_REQUIRED = "package must declare install rules: files or payload"


def payload_kind(source: BinaryIO) -> str:
    """按魔数判断上传载荷是 zip 归档还是单个 DLL。"""
    source.seek(0)
    magic = source.read(len(_ZIP_MAGIC))
    source.seek(0)
    return PAYLOAD_ZIP if magic == _ZIP_MAGIC else PAYLOAD_DLL


def typed_rules(install: dict[str, Any] | None) -> tuple[list[Any], list[Any]]:
    """包声明的 typed 规则：`(files 规则, payload 规则)`。

    两者互斥（写入时已校验），这里按缺省空表返回。
    """
    if not isinstance(install, dict):
        return [], []
    files = install.get("files")
    payload = install.get("payload")
    return (
        files if isinstance(files, list) else [],
        payload if isinstance(payload, list) else [],
    )


def validate_payload(source: BinaryIO, *, install: dict[str, Any] | None = None) -> None:
    """按包声明的安装规则校验上传载荷。

    规则是唯一的落点来源：没有 `install.files` 也没有 `install.payload` 直接拒绝。
    zip 归档的每条条目必须命中某条规则或被 `exclude` 跳过；单个 DLL 的落点由规则描述，
    这里只要求规则存在（条目路径要下载后由客户端按它自己的加载器注册表解析）。
    不合规直接抛 ValueError（上传阶段即拒绝，服务端因此是保证方）。
    """
    files_rules, payload_rules = typed_rules(install)
    if not files_rules and not payload_rules:
        raise ValueError(_RULES_REQUIRED)
    if payload_kind(source) != PAYLOAD_ZIP:
        return
    rules = payload_rules if payload_rules else files_rules
    declared = install or {}
    source.seek(0)
    try:
        with zipfile.ZipFile(source) as archive:
            names = [info.filename for info in archive.infolist() if not info.is_dir()]
    except zipfile.BadZipFile as exc:
        raise ValueError("package archive is not a zip archive") from exc
    for name in names:
        if _is_excluded(declared, name):
            continue
        if any(_matches(str(rule.get("match", "")), name) for rule in rules if isinstance(rule, dict)):
            continue
        raise ValueError(f"archive entry has no install rule: {name}")


def _matches(pattern: str, path: str) -> bool:
    """与客户端同一套匹配：整条路径或纯文件名，不区分大小写。"""
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    return fnmatch.fnmatchcase(normalized.casefold(), pattern.casefold()) or fnmatch.fnmatchcase(
        name.casefold(), pattern.casefold()
    )


def _is_excluded(install: dict[str, Any], source_name: str) -> bool:
    """客户端在规则之前先看 `exclude`：被跳过的条目不需要规则覆盖。"""
    patterns = install.get("exclude")
    if not isinstance(patterns, list):
        return False
    return any(_matches(str(pattern), source_name) for pattern in patterns)
