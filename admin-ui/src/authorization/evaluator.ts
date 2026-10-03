function normalizeNode(value: string): string {
    return value.trim().toLowerCase();
}

export function permissionMatches(pattern: string, requested: string): boolean {
    const normalizedPattern = normalizeNode(pattern);
    const normalizedRequested = normalizeNode(requested);
    if (!normalizedPattern || !normalizedRequested) return false;
    if (normalizedPattern === "*") return true;
    if (normalizedPattern.endsWith(".*")) {
        return normalizedRequested.startsWith(`${normalizedPattern.slice(0, -2)}.`);
    }
    return normalizedPattern === normalizedRequested;
}

export function permissionNode(resource: string, action: string): string {
    return `${resource.trim().replace(/\.$/, "")}.${action.trim()}`;
}

// 分配与模板写入按"资源级委派节点"授权：动作节点还原成所属资源的 `.grant`。
// 与后端 `resource_grant_node`（取最长已注册前缀资源的 `.grant`）一致；节点目录里
// 资源总是动作的父路径，所以去掉末段即可。
export function resourceGrantNode(node: string): string {
    const normalized = normalizeNode(node);
    const separator = normalized.lastIndexOf(".");
    if (separator <= 0) return `${normalized}.grant`;
    return `${normalized.slice(0, separator)}.grant`;
}
