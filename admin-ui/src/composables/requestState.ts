export type RequestPhase =
    | "idle"
    | "loading"
    | "success"
    | "empty"
    | "stale"
    | "forbidden"
    | "validation"
    | "server-error"
    | "error"
    | "cancelled";

export type RequestErrorKind =
    | "forbidden"
    | "validation"
    | "server-error"
    | "error";

export class RequestFailure extends Error {
    readonly kind: RequestErrorKind;
    readonly code: string | undefined;

    constructor(
        message: string,
        kind: RequestErrorKind = "error",
        code?: string,
    ) {
        super(message);
        this.name = "RequestFailure";
        this.kind = kind;
        this.code = code;
    }
}

export function classifyRequestError(cause: unknown): RequestFailure {
    if (cause instanceof RequestFailure) return cause;
    const {t} = useLocale();
    if (cause instanceof DOMException && cause.name === "AbortError") {
        return new RequestFailure(t("common.requestCancelled"), "error", "cancelled");
    }
    return new RequestFailure(
        cause instanceof Error ? cause.message : t("common.requestFailed"),
        "error",
    );
}

export function phaseForFailure(failure: RequestFailure): RequestPhase {
    if (failure.code === "cancelled") return "cancelled";
    return failure.kind;
}

export function responseFailure(response: {
    ok: boolean;
    error?: { code?: string; message?: string };
}): RequestFailure | null {
    if (response.ok) return null;
    const {t} = useLocale();
    const code = response.error?.code;
    const translated = code ? t(`common.errors.${code}`) : "";
    const message = translated && translated !== `common.errors.${code}`
        ? translated
        : t("common.requestFailed");
    if (
        code === "permission_denied"
        || code === "system_denied"
        || code === "team_permission_denied"
        || code === "team_access_denied"
    ) {
        return new RequestFailure(message, "forbidden", code);
    }
    if (code === "invalid_request" || code === "invalid_idempotency_key") {
        return new RequestFailure(message, "validation", code);
    }
    if (code === "invalid_session") {
        return new RequestFailure(message, "forbidden", code);
    }
    return new RequestFailure(message, "server-error", code);
}
import {useLocale} from "../i18n";
