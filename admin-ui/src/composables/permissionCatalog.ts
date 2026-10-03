/**
 * 权限节点目录的纯逻辑：树构建、节点格式校验、read 前置依赖。
 *
 * 后端契约（保持同步）：
 * - `domain.permissions.normalize_node`：小写、段内 `[a-z0-9][a-z0-9_-]{0,63}`、
 *   通配符只允许末段且只能有一个；
 * - `domain.permission_tree.validate_permission_tree`：单段节点没有动作段，无效；
 * - 模板与分配的写接口要求节点属于目录（`permission is not in the catalog`）
 *   且能解析到注册资源（`_resource_grant_node`），所以手动输入的目录外节点只是
 *   提示风险，不在客户端放行或拦截。
 */

export type PermissionTreeLeaf = {
    kind: "leaf";
    /** 后端 canonical 节点，例如 `team.example.packages.read`。 */
    node: string;
    /** 末段，例如 `read`、`*`。 */
    segment: string;
    path: string;
    depth: number;
};

export type PermissionTreeGroup = {
    kind: "group";
    segment: string;
    path: string;
    depth: number;
    children: PermissionTreeNode[];
    /** 该分组下全部可授予叶子节点。 */
    leaves: string[];
};

export type PermissionTreeNode = PermissionTreeGroup | PermissionTreeLeaf;

export type NodeValidation =
    | {ok: true; node: string}
    | {ok: false; reason: "empty" | "format" | "wildcard" | "noAction"};

const SEGMENT = /^[a-z0-9][a-z0-9_-]{0,63}$/;

/** 镜像 `normalize_node` + `validate_permission_tree`，用于手动输入的即时反馈。 */
export function validateNode(raw: string): NodeValidation {
    const node = raw.trim().toLowerCase();
    if (!node) return {ok: false, reason: "empty"};
    if (node === "*") return {ok: true, node};
    const parts = node.split(".");
    if (parts.some((part) => !part)) return {ok: false, reason: "format"};
    if (parts.filter((part) => part === "*").length > 1) {
        return {ok: false, reason: "wildcard"};
    }
    if (parts.slice(0, -1).includes("*")) return {ok: false, reason: "wildcard"};
    const segments = parts[parts.length - 1] === "*" ? parts.slice(0, -1) : parts;
    if (!segments.length) return {ok: false, reason: "wildcard"};
    if (segments.some((part) => !SEGMENT.test(part))) {
        return {ok: false, reason: "format"};
    }
    // 单段节点没有动作段，无法解析到资源，后端会拒绝。
    if (parts.length < 2) return {ok: false, reason: "noAction"};
    return {ok: true, node};
}

function compareSegment(left: string, right: string): number {
    if (left === right) return 0;
    if (left === "*") return 1;
    if (right === "*") return -1;
    return left.localeCompare(right);
}

function collectLeaves(nodes: readonly PermissionTreeNode[]): string[] {
    const values: string[] = [];
    for (const node of nodes) {
        if (node.kind === "leaf") {
            values.push(node.node);
        } else {
            values.push(...node.leaves);
        }
    }
    return values;
}

function buildLevel(nodes: readonly string[], depth: number): PermissionTreeNode[] {
    const buckets = new Map<string, {leaf: string | null; children: string[]}>();
    for (const node of nodes) {
        const parts = node.split(".");
        const segment = parts[depth];
        if (segment === undefined) continue;
        const bucket = buckets.get(segment) ?? {leaf: null, children: []};
        if (parts.length === depth + 1) {
            bucket.leaf = node;
        } else {
            bucket.children.push(node);
        }
        buckets.set(segment, bucket);
    }
    const result: PermissionTreeNode[] = [];
    for (const [segment, bucket] of [...buckets.entries()].sort((left, right) =>
        compareSegment(left[0], right[0]))) {
        if (bucket.leaf) {
            result.push({
                kind: "leaf",
                node: bucket.leaf,
                segment,
                path: bucket.leaf,
                depth,
            });
        }
        if (bucket.children.length) {
            const children = buildLevel(bucket.children, depth + 1);
            result.push({
                kind: "group",
                segment,
                path: [...bucket.children[0].split(".").slice(0, depth), segment].join("."),
                depth,
                children,
                leaves: collectLeaves(children),
            });
        }
    }
    return result;
}

/** 把扁平节点目录折叠成分组树；中间路径只作为分组，不可单独授予。 */
export function buildPermissionTree(options: readonly string[]): PermissionTreeNode[] {
    const unique = [...new Set(options.map((option) => option.trim().toLowerCase()).filter(Boolean))];
    return buildLevel(unique, 0);
}

export function isWildcardNode(node: string): boolean {
    return node === "*" || node.endsWith(".*");
}