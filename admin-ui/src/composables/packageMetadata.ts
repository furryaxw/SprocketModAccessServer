// Package metadata 在管理端就是一份 JSON 文档：它是公开 v3 条目里属于包的那部分。
//
// 管理端只负责这段文档本身；`id`/`schema_version`/`releases`/`signature` 由服务端从包与版本产出，
// 公开目录的抓取与推荐语义（`release`/`featured`）在私有侧不被接受。

export type Metadata = Record<string, unknown>;

// 文档里出现这些键时说明拿错了文档（多半是直接粘了公开条目）：保存前摘掉并提示。
export const SERVER_OWNED_KEYS = [
    "$schema",
    "schema_version",
    "id",
    "release",
    "featured",
    "releases",
    "signature",
] as const;

// 初始文档：与公开侧的 sprocket-mod.example.json 同形，去掉服务端自有的键。
export const EXAMPLE_METADATA = {
    name: "ExampleSprocketMod",
    authors: ["ExampleAuthor"],
    repository: "ExampleAuthor/ExampleSprocketMod",
    license: "MIT",
    display_name: {"en": "Example Sprocket Mod"},
    description: {"en": "Example Sprocket Mod"},
    dependencies: [],
    recommendations: [],
    install: {
        files: [{match: "*.dll", type: "melonloader:mod"}],
        scan_dlls: true,
        exclude: [],
    },
    category: "gameplay",
    tags: ["example"],
};

export function formatMetadata(value: Metadata): string {
    return `${JSON.stringify(value, null, 2)}\n`;
}

export function newExampleMetadata(): Metadata {
    return JSON.parse(JSON.stringify(EXAMPLE_METADATA)) as Metadata;
}

export type ParseFailure = {code: "empty" | "object" | "syntax"; detail: string};
export type ParseResult = {value: Metadata; failure: null} | {value: null; failure: ParseFailure};

export function parseMetadata(text: string): ParseResult {
    const trimmed = text.trim();
    if (!trimmed) return {value: null, failure: {code: "empty", detail: ""}};
    let parsed: unknown;
    try {
        parsed = JSON.parse(trimmed);
    } catch (cause) {
        return {value: null, failure: {code: "syntax", detail: cause instanceof Error ? cause.message : String(cause)}};
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        return {value: null, failure: {code: "object", detail: ""}};
    }
    return {value: parsed as Metadata, failure: null};
}

export function stripServerOwnedKeys(value: Metadata): {metadata: Metadata; dropped: string[]} {
    const metadata: Metadata = {};
    const dropped: string[] = [];
    for (const [key, item] of Object.entries(value)) {
        if ((SERVER_OWNED_KEYS as readonly string[]).includes(key)) {
            dropped.push(key);
            continue;
        }
        metadata[key] = item;
    }
    return {metadata, dropped};
}
