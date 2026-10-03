export function sortBy<T>(
    items: readonly T[],
    selector: (item: T) => string | number,
): T[] {
    return [...items].sort((left, right) => {
        const a = selector(left);
        const b = selector(right);
        return a > b ? 1 : a < b ? -1 : 0;
    });
}

export function filterText<T>(
    items: readonly T[],
    selector: (item: T) => string,
    query: string,
): T[] {
    const normalized = query.trim().toLowerCase();
    return normalized
        ? items.filter((item) => selector(item).toLowerCase().includes(normalized))
        : [...items];
}

export function paginate<T>(
    items: readonly T[],
    page: number,
    pageSize: number,
): T[] {
    const start = Math.max(0, page - 1) * Math.max(1, pageSize);
    return items.slice(start, start + Math.max(1, pageSize));
}

export function deriveVisible<T>(
    items: readonly T[],
    canRead: (item: T) => boolean,
): T[] {
    return items.filter(canRead);
}
