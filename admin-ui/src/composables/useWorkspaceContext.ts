import {computed, ref} from "vue";
import {logger} from "../api/logger";
import {transport, type TransportResponse} from "../api/transport";
import {responseFailure} from "./requestState";
import {useRequest} from "./useRequest";
import {useLocale} from "../i18n";

export type WorkspaceKind = "system" | "team";

export type WorkspaceOption = {
    team_id: string;
    name: string;
    workspace_kind: WorkspaceKind;
    owner_user_id?: string;
};

type StoredWorkspace = {
    kind: WorkspaceKind;
    teamId: string;
};

const workspaceKey = "sprocket.access.workspace";
const request = useRequest<WorkspaceOption[]>();
const {t} = useLocale();
const options = ref<WorkspaceOption[]>([]);
const selection = ref<WorkspaceOption | null>(null);
const selectionError = ref<string | null>(null);
let selectionVersion = 0;
let optionsLoadPromise: Promise<WorkspaceOption[]> | null = null;
let hasLoadedOptions = false;

function parseStoredWorkspace(value: string | null): StoredWorkspace | null {
    if (!value) return null;
    try {
        const parsed = JSON.parse(value) as Partial<StoredWorkspace>;
        if (
            (parsed.kind === "system" || parsed.kind === "team")
            && typeof parsed.teamId === "string"
            && parsed.teamId
        ) {
            return {kind: parsed.kind, teamId: parsed.teamId};
        }
    } catch (cause) {
        logger.warn("workspace.context.storage_parse_failed", {
            message: cause instanceof Error ? cause.message : String(cause),
        });
    }
    return null;
}

function readWorkspaceCookie(): string | null {
    const entry = document.cookie
        .split("; ")
        .find((item) => item.startsWith(`${workspaceKey}=`));
    return entry
        ? decodeURIComponent(entry.slice(workspaceKey.length + 1))
        : null;
}

function writeWorkspaceCookie(value: string | null) {
    document.cookie = value
        ? `${workspaceKey}=${encodeURIComponent(value)}; Path=/; SameSite=Lax`
        : `${workspaceKey}=; Max-Age=0; Path=/; SameSite=Lax`;
}

function readStoredWorkspace(): StoredWorkspace | null {
    try {
        return parseStoredWorkspace(readWorkspaceCookie())
            ?? parseStoredWorkspace(localStorage.getItem(workspaceKey));
    } catch (cause) {
        logger.warn("workspace.context.storage_read_failed", {
            message: cause instanceof Error ? cause.message : String(cause),
        });
        return null;
    }
}

function writeStoredWorkspace(option: WorkspaceOption | null) {
    try {
        if (!option) {
            localStorage.removeItem(workspaceKey);
            writeWorkspaceCookie(null);
            return;
        }
        const value = JSON.stringify({
            kind: option.workspace_kind,
            teamId: option.team_id,
        });
        localStorage.setItem(workspaceKey, value);
        writeWorkspaceCookie(value);
    } catch (cause) {
        logger.warn("workspace.context.storage_write_failed", {
            message: cause instanceof Error ? cause.message : String(cause),
        });
    }
}

function sameWorkspace(
    left: WorkspaceOption | StoredWorkspace | null,
    right: WorkspaceOption | null,
): boolean {
    if (!left || !right) return false;
    const leftKind =
        "workspace_kind" in left ? left.workspace_kind : left.kind;
    const leftTeamId = "team_id" in left ? left.team_id : left.teamId;
    return leftKind === right.workspace_kind && leftTeamId === right.team_id;
}

function sameWorkspaceOption(
    left: WorkspaceOption | null,
    right: WorkspaceOption | null,
): boolean {
    if (!left || !right) return false;
    return sameWorkspace(left, right)
        && left.name === right.name
        && left.owner_user_id === right.owner_user_id;
}

function parseWorkspaceOptions(response: TransportResponse): WorkspaceOption[] {
    const failure = responseFailure(response);
    if (failure) throw failure;
    const values = response.data?.teams;
    if (!Array.isArray(values)) {
        throw new Error(t("common.workspaceResponseInvalid"));
    }
    return values.filter((value): value is WorkspaceOption => {
        if (!value || typeof value !== "object") return false;
        const item = value as Record<string, unknown>;
        return (
            typeof item.team_id === "string"
            && item.team_id.length > 0
            && typeof item.name === "string"
            && item.name.length > 0
            && (item.workspace_kind === "system" || item.workspace_kind === "team")
        );
    });
}

async function commitWorkspace(option: WorkspaceOption, version: number): Promise<boolean> {
    const response = await transport.selectTeam(option.team_id);
    if (version !== selectionVersion) {
        return false;
    }
    if (!response.ok) {
        selectionError.value =
            responseFailure(response)?.message ?? t("common.workspaceSwitchFailed");
        return false;
    }
    selection.value = option;
    writeStoredWorkspace(option);
    selectionError.value = null;
    logger.info("workspace.context.selected", {
        kind: option.workspace_kind,
        teamId: option.team_id,
    });
    return true;
}

export function useWorkspaceContext() {
    const selected = computed(() => selection.value);
    const phase = computed(() => request.phase.value);
    const error = computed(
        () => selectionError.value ?? request.error.value?.message ?? null,
    );

    async function loadOptionsInternal(
        {preserveSelection = false}: {preserveSelection?: boolean} = {},
    ) {
        selectionError.value = null;
        const versionAtStart = selectionVersion;
        logger.info("workspace.options.load.start");
        try {
            const loaded = await request.run(
                async (signal) => {
                    const response = await transport.request({
                        action: "read",
                        node: "system.authentication.me.teams",
                    });
                    if (signal.aborted) {
                        throw new DOMException(t("common.requestCancelled"), "AbortError");
                    }
                    return parseWorkspaceOptions(response);
                },
                {isEmpty: (items) => items.length === 0},
            );
            options.value = loaded;
            const hadSelection = selection.value !== null;
            logger.info("workspace.options.loaded", {count: loaded.length});

            if (loaded.length === 0) {
                selection.value = null;
                writeStoredWorkspace(null);
                transport.clearTeamContext();
                return loaded;
            }

            if (hadSelection) {
                const current = loaded.find((item) => sameWorkspace(item, selection.value));
                if (current) {
                    // 内容没变就不换引用：selectedWorkspace 是 AppShell 观察者的依赖，
                    // 每轮列表刷新都赋一个新对象会被读成"换了工作区"。
                    if (!sameWorkspaceOption(current, selection.value)) {
                        selection.value = current;
                    }
                    writeStoredWorkspace(current);
                    return loaded;
                }
                // 这份结果可能来自与"选择已变化"竞争的旧请求：版本变了就不动选择，
                // 否则会先清空再重选，内容区要闪一帧（表现为页面抽搐）。
                if (selectionVersion !== versionAtStart) {
                    return loaded;
                }
                if (preserveSelection) {
                    selection.value = null;
                    writeStoredWorkspace(null);
                    // 归档/移除当前工作区后同步清除 transport 上下文，
                    // 避免后续页面用陈旧 teamId 刷新授权快照。
                    transport.clearTeamContext();
                }
            }

            if (selectionVersion !== versionAtStart) {
                return loaded;
            }

            if (preserveSelection && hasLoadedOptions) {
                return loaded;
            }

            const stored = readStoredWorkspace();
            const candidate = loaded.find((item) => sameWorkspace(stored, item))
                ?? loaded.find((item) => item.workspace_kind === "system")
                ?? loaded[0];
            if (candidate && await commitWorkspace(candidate, versionAtStart)) {
                hasLoadedOptions = true;
                return loaded;
            }
            selection.value = null;
            writeStoredWorkspace(null);
            hasLoadedOptions = true;
            return loaded;
        } catch (cause) {
            logger.warn("workspace.options.load.failed", {
                message: cause instanceof Error ? cause.message : String(cause),
            });
            throw cause;
        }
    }

    async function loadOptions(
        options: {preserveSelection?: boolean} = {},
    ): Promise<WorkspaceOption[]> {
        if (optionsLoadPromise) {
            return optionsLoadPromise;
        }
        optionsLoadPromise = loadOptionsInternal(options).finally(() => {
            optionsLoadPromise = null;
        });
        return optionsLoadPromise;
    }

    async function selectWorkspace(option: WorkspaceOption) {
        if (!options.value.some((item) => sameWorkspace(item, option))) {
            selectionError.value = t("common.workspaceUnavailable");
            logger.warn("workspace.context.select.rejected", {
                reason: "not_in_backend_options",
            });
            return false;
        }
        logger.info("workspace.context.select.start", {
            kind: option.workspace_kind,
            teamId: option.team_id,
        });
        const version = ++selectionVersion;
        try {
            const selectedSuccessfully = await commitWorkspace(option, version);
            if (!selectedSuccessfully) {
                logger.warn("workspace.context.select.denied", {
                    teamId: option.team_id,
                });
            }
            return selectedSuccessfully;
        } catch (cause) {
            selectionError.value =
                cause instanceof Error ? cause.message : String(cause);
            logger.error("workspace.context.select.error", {
                message: selectionError.value,
            });
            return false;
        }
    }

    function reset() {
        selectionVersion++;
        optionsLoadPromise = null;
        options.value = [];
        selection.value = null;
        hasLoadedOptions = false;
        selectionError.value = null;
        writeStoredWorkspace(null);
        request.reset();
    }

    return {
        options: computed(() => options.value),
        selected,
        phase,
        error,
        loading: request.loading,
        loadOptions,
        selectWorkspace,
        retry: request.retry,
        cancel: request.cancel,
        reset,
    };
}
