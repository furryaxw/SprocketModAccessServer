"""零依赖的 JSON Schema 子集校验器。

只支持本项目 schema 用到的关键字：type（含类型数组）/ required / properties / additionalProperties /
items / minItems / maxItems / minProperties / minLength / pattern / enum / const / minimum / anyOf /
$ref（同文件 JSON Pointer 与相对文件引用）。环境里没有 jsonschema，因此自带一个可读的实现，
让 schema 真正参与测试，而不是只当文档。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_TYPE_CHECKS = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "boolean": lambda value: isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "null": lambda value: value is None,
}


class SchemaError(AssertionError):
    """文档不符合 schema。"""


def load_schema(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _pointer(document: dict[str, Any], pointer: str) -> dict[str, Any]:
    node: Any = document
    for token in pointer.lstrip("/").split("/") if pointer.strip("/") else []:
        node = node[token.replace("~1", "/").replace("~0", "~")]
    if not isinstance(node, dict):
        raise SchemaError(f"schema reference does not resolve to an object: {pointer}")
    return node


def _resolve(ref: str, root: dict[str, Any], base: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    """返回（目标 schema, 该 schema 所在文档根, 该文档所在目录）。"""
    file_part, _, pointer = ref.partition("#")
    if not file_part:
        return _pointer(root, pointer), root, base
    path = base / file_part
    document = json.loads(path.read_text(encoding="utf-8"))
    return _pointer(document, pointer), document, path.parent


def validate(value: Any, schema: dict[str, Any], *, root: dict[str, Any] | None = None,
             base: Path | None = None, path: str = "$") -> None:
    root = schema if root is None else root
    base = Path(".") if base is None else base
    if "$ref" in schema:
        target, target_root, target_base = _resolve(str(schema["$ref"]), root, base)
        validate(value, target, root=target_root, base=target_base, path=path)
        return
    if "const" in schema and value != schema["const"]:
        raise SchemaError(f"{path}: expected {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{path}: {value!r} is not one of {schema['enum']}")
    if "anyOf" in schema:
        for candidate in schema["anyOf"]:
            try:
                validate(value, candidate, root=root, base=base, path=path)
                break
            except SchemaError:
                continue
        else:
            raise SchemaError(f"{path}: matches none of the anyOf schemas")
    expected_type = schema.get("type")
    if expected_type is not None:
        expected = [expected_type] if isinstance(expected_type, str) else list(expected_type)
        checkers = []
        for name in expected:
            checker = _TYPE_CHECKS.get(str(name))
            if checker is None:
                raise SchemaError(f"{path}: unsupported schema type {name!r}")
            checkers.append(checker)
        if not any(checker(value) for checker in checkers):
            names = " or ".join(str(name) for name in expected)
            raise SchemaError(f"{path}: expected {names}, got {type(value).__name__}")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                raise SchemaError(f"{path}: required property {name!r} is missing")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for name, item in value.items():
            child = f"{path}.{name}"
            if name in properties:
                validate(item, properties[name], root=root, base=base, path=child)
            elif additional is False:
                raise SchemaError(f"{child}: additional property is not allowed")
            elif isinstance(additional, dict):
                validate(item, additional, root=root, base=base, path=child)
        minimum_properties = schema.get("minProperties")
        if minimum_properties is not None and len(value) < minimum_properties:
            raise SchemaError(f"{path}: needs at least {minimum_properties} properties")
    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        if minimum_items is not None and len(value) < minimum_items:
            raise SchemaError(f"{path}: needs at least {minimum_items} items")
        maximum_items = schema.get("maxItems")
        if maximum_items is not None and len(value) > maximum_items:
            raise SchemaError(f"{path}: allows at most {maximum_items} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate(item, item_schema, root=root, base=base, path=f"{path}[{index}]")
    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        if minimum_length is not None and len(value) < minimum_length:
            raise SchemaError(f"{path}: shorter than {minimum_length}")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, value) is None:
            raise SchemaError(f"{path}: {value!r} does not match {pattern}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            raise SchemaError(f"{path}: {value} is below the minimum {minimum}")
