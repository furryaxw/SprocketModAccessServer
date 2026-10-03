import {computed} from "vue";
import {transport, type TransportResponse} from "../api/transport";
import {logger} from "../api/logger";
import {permissionMatches} from "../authorization/evaluator";
import {
    parseAuthorizationSnapshot,
    type AuthorizationSnapshot,
} from "../authorization/types";
import {
    phaseForFailure,
    responseFailure,
    RequestFailure,
    type RequestPhase,
} from "./requestState";
import {useRequest} from "./useRequest";
import {useLocale} from "../i18n";

const request = useRequest<AuthorizationSnapshot>();
let refreshPromise: Promise<AuthorizationSnapshot> | null = null;
let refreshedAt = 0;

function snapshotFromResponse(response: TransportResponse): AuthorizationSnapshot {
    const failure = responseFailure(response);
    if (failure) throw failure;
    return parseAuthorizationSnapshot(response.data);
}

export function useAuthorization() {
    const {t} = useLocale();
    const snapshot = computed(() => request.data.value);
    const phase = computed<RequestPhase>(() => request.phase.value);
    const ready = computed(
        () => phase.value === "success" && snapshot.value !== null,
    );

    function permissions(): readonly string[] {
        return snapshot.value?.effective_permissions ?? [];
    }

    function can(node: string): boolean {
        if (!ready.value) return false;
        return permissions().some((permission) => permissionMatches(permission, node));
    }

    function canAny(nodes: readonly string[]): boolean {
        return nodes.some(can);
    }

    function canAll(nodes: readonly string[]): boolean {
        return nodes.every(can);
    }

    function canGrant(node: string): boolean {
        if (!ready.value) return false;
        return (snapshot.value?.grantable_permissions ?? []).some((permission) =>
            permissionMatches(permission, node),
        );
    }

    function markStale() {
        if (request.data.value !== null) {
            request.markStale();
        }
    }

    // 页面挂载不是快照刷新触发点（见 runtime-communication-contract 的刷新时机清单）：
    // 刚刷新过、且快照作用域与当前工作区一致的快照直接复用，避免一次导航里 AppShell 与
    // 页面各拉一轮快照，让依赖快照的按钮连续重排两次。传入的 teamId 只是调用方意图说明，
    // 真正的作用域来自 WebSocket 连接的 team context。变更后的刷新与手动刷新用 refresh。
    const ensureWindowMs = 2000;

    async function ensure(_teamId: string | null = transport.selectedTeamId) {
        const current = snapshot.value;
        if (!current || phase.value !== "success" || Date.now() - refreshedAt >= ensureWindowMs) {
            return refresh();
        }
        const scope = current.team?.team_id ?? null;
        if (scope !== (transport.selectedTeamId ?? null)) {
            return refresh();
        }
        return current;
    }

    async function refresh(teamId: string | null = transport.selectedTeamId) {
        logger.info("authorization.snapshot.refresh.start", {
            teamContext: Boolean(teamId),
        });
        if (refreshPromise) {
            return refreshPromise;
        }
        refreshPromise = request.run(async (signal) => {
                const response = await transport.request({
                    action: "read",
                    node: "system.authentication.me.authorization",
                });
                if (signal.aborted) {
                    throw new RequestFailure(t("common.requestCancelled"), "error", "cancelled");
                }
                return snapshotFromResponse(response);
            }).finally(() => {
                refreshPromise = null;
            });
        try {
            const result = await refreshPromise;
            refreshedAt = Date.now();
            logger.info("authorization.snapshot.refresh.success", {
                teamContext: Boolean(result.team),
            });
            return result;
        } catch (cause) {
            logger.warn("authorization.snapshot.refresh.failed", {
                phase: phaseForFailure(
                    cause instanceof RequestFailure
                        ? cause
                        : new RequestFailure(String(cause)),
                ),
            });
            throw cause;
        }
    }

    function reset() {
        request.reset();
    }

    return {
        snapshot,
        phase,
        ready,
        can,
        canAny,
        canAll,
        canGrant,
        markStale,
        refresh,
        ensure,
        retry: request.retry,
        cancel: request.cancel,
        reset,
        error: request.error,
    };
}
