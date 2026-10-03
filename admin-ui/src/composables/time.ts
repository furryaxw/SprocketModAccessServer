export function timestampToDate(value: number | string | Date): Date | null {
    if (value instanceof Date) {
        return Number.isNaN(value.getTime()) ? null : value;
    }
    const numeric = typeof value === "number" ? value : Number(value);
    if (!Number.isFinite(numeric) || numeric <= 0) return null;
    const milliseconds =
        Math.abs(numeric) < 100000000000 ? numeric * 1000 : numeric;
    const date = new Date(milliseconds);
    return Number.isNaN(date.getTime()) ? null : date;
}

export function formatTimestamp(
    value: number | string | Date | null | undefined,
    locale: string,
): string {
    if (value === null || value === undefined) return "—";
    const date = timestampToDate(value);
    if (!date) return "—";
    return new Intl.DateTimeFormat(locale, {
        dateStyle: "medium",
        timeStyle: "short",
    }).format(date);
}
