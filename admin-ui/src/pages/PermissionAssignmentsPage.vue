<script lang="ts" setup>
import {computed, onMounted, ref, watch} from "vue";
import {useRoute} from "vue-router";
import {transport, type TransportResponse} from "../api/transport";
import {RequestFailure, responseFailure, type RequestPhase} from "../composables/requestState";
import {useAuthorization} from "../composables/useAuthorization";
import {useRequest} from "../composables/useRequest";
import {useWorkspaceContext} from "../composables/useWorkspaceContext";
import UiDataTable from "../components/UiDataTable.vue";
import RequestState from "../components/RequestState.vue";
import {useLocale} from "../i18n";
import PageHeader from "../components/PageHeader.vue";
import UiInput from "../components/UiInput.vue";
import UiButton from "../components/UiButton.vue";
import UiDialog from "../components/UiDialog.vue";
import {useToast} from "../composables/useToast";
import {formatTimestamp} from "../composables/time";
import PermissionTreeEditor from "../components/PermissionTreeEditor.vue";
import ValidityInput from "../components/ValidityInput.vue";
import {resourceGrantNode} from "../authorization/evaluator";

type Assignment = {
    assignment_id: string;
    grant_id: string;
    github_user_id: string;
    node: string;
    effect: string;
    priority: number;
    grant_effect: string;
    source_type?: string;
    source_id?: string;
    status?: string;
    expires_at?: number | null;
    created_at?: number;
    grantable?: boolean;
    origin?: string;
    template_id?: string | null;
};

const messages = {
    zh: {
        permissionPlaceholder: "权限节点",

        title: "权限分配",
        description: "按用户、权限节点和授权来源检查当前 Team 的具体授权记录。",

        refresh: "刷新",
        user: "用户",
        node: "权限节点",
        source: "来源",
        effect: "效果",
        priority: "优先级",

        grant: "分发效果",
        grantable: "当前可分发",

        allow: "允许",
        deny: "拒绝",
        group: "按用户分组",

        searchUser: "过滤用户 ID",
        searchNode: "过滤权限节点",
        searchSource: "过滤模板或来源",

        searchStatus: "过滤状态",

        searchExpiry: "过滤有效期",

        allStatuses: "全部状态",

        allExpiry: "全部有效期",

        expired: "已过期",

        permanent: "永久",
        clearFilters: "清除过滤器",

        previousPage: "上一页",

        nextPage: "下一页",

        page: "页",

        create: "新建分配",
        createTitle: "新建权限分配",
        createConfirm: "创建",
        userPlaceholder: "用户 ID",
        edit: "编辑",
        editTitle: "编辑权限分配",
        templateRowHint: "模板套用行的节点由模板决定；改节点请到权限模板页或用户权限节点。",
        close: "关闭",
        save: "保存修改",
        status: "状态",

        active: "启用",
        suspended: "已暂停",

        revoked: "已撤销",
        suspend: "暂停",
        restore: "恢复",
        revoke: "撤销",
        extend: "延长有效期",

        extendTitle: "延长权限分配有效期",

        expiresAt: "有效期截止时间戳",
        confirmInvalid: "确认令牌响应无效。",

        noExpiry: "永久",
        noWorkspace: "权限分配需要先选择工作区。",

        permission: "没有查看权限分配的权限。",

        invalid: "权限分配响应格式无效。",

        created: "创建时间",
        lifecycleTitle: "确认分配变更",
        lifecycleBody: "此操作将立即生效：",

        lifecycleUser: "目标用户",
        lifecycleId: "分配 ID",
        lifecycleStatus: "当前状态",

        lifecycleAction: "操作",
        lifecycleNodes: "将失去的权限节点",
        lifecycleImpact: "可能失去工作区或页面访问",
        lifecycleConfirm: "确认",
        lifecycleSuspend: "暂停后该分配的权限不再生效，可恢复。",

        lifecycleRevoke: "撤销后该分配永久停止生效，不能恢复。",

        noNodes: "—",

    },
    en: {
        permissionPlaceholder: "Permission nodes",
        title: "Permission assignments",
        description: "Inspect concrete authorization records by user, node, and source.",
        refresh: "Refresh",
        user: "User",
        node: "Permission node",
        source: "Source",
        effect: "Effect",
        priority: "Priority",
        grant: "Grant effect",
        grantable: "Grantable now",
        allow: "Allow",
        deny: "Deny",
        group: "Grouped by user",
        assignmentCount: "{count} permission records",
        searchUser: "Filter by user ID",
        searchNode: "Filter by permission node",
        searchSource: "Filter by template or source",
        searchStatus: "Filter by status",
        searchExpiry: "Filter by expiry",
        allStatuses: "All statuses",
        allExpiry: "All expiry types",
        expired: "Expired",
        permanent: "Permanent",
        clearFilters: "Clear filters",
        previousPage: "Previous page",
        nextPage: "Next page",
        page: "Page",
        create: "Create assignment",
        createTitle: "Create permission assignment",
        createConfirm: "Create",
        userPlaceholder: "User ID",
        edit: "Edit",
        editTitle: "Edit permission assignment",
        templateRowHint: "Nodes on a template-applied row come from the template; change them on the template page or in the user's permission nodes.",
        close: "Close",
        save: "Save changes",
        status: "Status",
        active: "Active",
        suspended: "Suspended",
        revoked: "Revoked",
        suspend: "Suspend",
        restore: "Restore",
        revoke: "Revoke",
        extend: "Extend expiry",
        extendTitle: "Extend permission assignment expiry",
        expiresAt: "Expiry timestamp",
        confirmInvalid: "The confirmation response is invalid.",
        noExpiry: "Permanent",
        noWorkspace: "Select a workspace to view permission assignments.",
        permission: "You do not have permission to view permission assignments.",
        invalid: "The permission assignments response is invalid.",
        created: "Created",
        lifecycleTitle: "Confirm assignment change",
        lifecycleBody: "This takes effect immediately:",
        lifecycleUser: "Target user",
        lifecycleId: "Assignment ID",
        lifecycleStatus: "Current status",
        lifecycleAction: "Action",
        lifecycleNodes: "Permission nodes lost",
        lifecycleImpact: "May lose workspace or page access",
        lifecycleConfirm: "Confirm",
        lifecycleSuspend: "Suspending stops this assignment's permissions until restored.",
        lifecycleRevoke: "Revoking permanently stops this assignment and it cannot be restored.",
        noNodes: "—",

        permissionsAdded: "Permissions added",
        permissionsRemoved: "Permissions removed",
        expiryChanged: "Expiry changed",
        effectChanged: "Effect changed",
        priorityChanged: "Priority changed",
        affectedUsers: "Affected users",
        accessImpact: "Access impact",
        noChanges: "No permission changes",
        yes: "Yes",
        no: "No",
    },
} as const;

const zhAssignmentMessages: Record<string, string> = {
    permissionPlaceholder: "权限节点",

    title: "权限分配",
    description: "按用户、权限节点和授权来源检查当前 Team 的具体授权记录。",

    refresh: "刷新",
    user: "用户",
    node: "权限节点",
    source: "来源",
    effect: "效果",
    priority: "优先级",

    grant: "分发效果",
    grantable: "当前可分发",

    allow: "允许",
    deny: "拒绝",
    group: "按用户分组",

    assignmentCount: "{count} 条权限记录",

    searchUser: "过滤用户 ID",
    searchNode: "过滤权限节点",
    searchSource: "过滤模板或来源",

    searchStatus: "过滤状态",

    searchExpiry: "过滤有效期",

    allStatuses: "全部状态",

    allExpiry: "全部有效期",

    expired: "已过期",

    permanent: "永久",
    clearFilters: "清除过滤器",

    previousPage: "上一页",

    nextPage: "下一页",

    page: "页",

    create: "新建分配",
    createTitle: "新建权限分配",
    createConfirm: "创建",
    userPlaceholder: "用户 ID",
    edit: "编辑",
    editTitle: "编辑权限分配",
    close: "关闭",
    save: "保存修改",
    status: "状态",

    active: "启用",
    suspended: "已暂停",

    revoked: "已撤销",
    suspend: "暂停",
    restore: "恢复",
    revoke: "撤销",
    extend: "延长有效期",

    extendTitle: "延长权限分配有效期",

    expiresAt: "有效期截止时间戳",
    confirmInvalid: "确认令牌响应无效。",

    noExpiry: "永久",
    noWorkspace: "权限分配需要先选择工作区。",

    permission: "没有查看权限分配的权限。",

    invalid: "权限分配响应格式无效。",

    created: "创建时间",
    permissionsAdded: "新增权限",
    permissionsRemoved: "移除权限",
    expiryChanged: "有效期变化",

    effectChanged: "效果变化",
    priorityChanged: "优先级变化",

    affectedUsers: "影响用户",
    accessImpact: "访问影响",
    noChanges: "没有权限变化",
    yes: "是",

    no: "否",

};

const {locale, t: baseT} = useLocale();
const authorization = useAuthorization();
const {selected} = useWorkspaceContext();
type AssignmentPage = {
    items: Assignment[];
    total: number;
    limit: number;
    offset: number;
};

const request = useRequest<AssignmentPage>();
const route = useRoute();
const catalog = ref<string[]>([]);
// Team 工作区只展示本 Team 的节点：目录是全平台的，不裁剪就会把别的 Team 与系统节点
// 摆在面前，看起来"可以分发 Team 之外的权限"。
const editorCatalog = computed(() => {
    const current = selected.value;
    return current && current.workspace_kind !== "system"
        ? catalog.value.filter((value) => value.startsWith(`team.${current.team_id}.`))
        : catalog.value;
});
const toast = useToast();
const permissionFailure = ref<RequestFailure | null>(null);
const userFilter = ref("");
const nodeFilter = ref("");
const sourceFilter = ref("");
// Overview expired-grants 指标跳转携带 ?status=expired，首载时预置状态筛选。
const statusFilter = ref(
    route.query.status === "expired" ? "expired" : "",
);
const expiryFilter = ref("");
const page = ref(1);
const pageSize = 50;
const editOpen = ref(false);
const selectedAssignment = ref<Assignment | null>(null);
const editPermissions = ref<Record<string, string>>({});
const editExpiresAt = ref<number | null>(null);
const createOpen = ref(false);
const createUserId = ref("");
const createPermissions = ref<Record<string, string>>({});
const createExpiresAt = ref<number | null>(null);
const mutationPending = ref(false);
const mutationError = ref<string | null>(null);
const workspace = computed(() => selected.value);
const localeName = computed(() => locale.value === "zh" ? "zh-CN" : "en-US");
const totalAssignments = computed(() => request.data.value?.total ?? 0);
const pageCount = computed(() => Math.max(1, Math.ceil(totalAssignments.value / pageSize)));
const node = computed(() => workspace.value
    ? `team.${workspace.value.team_id}.permission_assignments`
    : "");
const canRead = computed(() => node.value ? authorization.can(`${node.value}.read`) : false);
const canManage = computed(() => node.value ? authorization.can(`${node.value}.manage`) : false);
const phase = computed<RequestPhase>(() => {
    if (!workspace.value) return "empty";
    if (permissionFailure.value) return "forbidden";
    if (authorization.phase.value !== "success") return authorization.phase.value;
    if (!canRead.value) return "forbidden";
    return request.phase.value;
});
const editDiff = computed(() => {
    const assignment = selectedAssignment.value;
    if (!assignment) return null;
    const currentPermissions = new Set(
        filteredAssignments.value
            .filter((item) => item.grant_id === assignment.grant_id)
            .map((item) => item.node),
    );
    const currentEffects = new Map(
        filteredAssignments.value
            .filter((item) => item.grant_id === assignment.grant_id)
            .map((item) => [item.node, item.effect === "deny" ? "deny" : "allow"]),
    );
    const updatedPermissions = new Set(Object.keys(editPermissions.value));
    const added = [...updatedPermissions].filter((item) => !currentPermissions.has(item)).sort();
    const removed = [...currentPermissions].filter((item) => !updatedPermissions.has(item)).sort();
    // 两边都在的节点，effect 变了才算"效果变化"（deny 覆盖 allow 就是这种情况）。
    const effectChanged = [...updatedPermissions].some((node) =>
        currentPermissions.has(node)
        && (editPermissions.value[node] === "deny" ? "deny" : "allow") !== currentEffects.get(node));
    const deniedAdded = added.filter((node) => editPermissions.value[node] === "deny");
    const currentExpiry = assignment.expires_at ?? null;
    const nextExpiry = editExpiresAt.value;
    const expiryChanged = nextExpiry !== currentExpiry;
    const removesAccess = removed.length > 0 || deniedAdded.length > 0 || effectChanged || (
        expiryChanged
        && currentExpiry === null
        && nextExpiry !== null
    ) || (
        expiryChanged
        && currentExpiry !== null
        && nextExpiry !== null
        && nextExpiry < currentExpiry
    );
    return {
        added,
        removed,
        expiryChanged,
        effectChanged,
        priorityChanged: false,
        affectedUsers: [assignment.github_user_id],
        accessImpact: {
            addsAccess: added.length > deniedAdded.length,
            removesAccess,
            changesExpiry: expiryChanged,
        },
    };
});

function pageT(key: string) {
    if (locale.value === "zh" && zhAssignmentMessages[key]) {
        return zhAssignmentMessages[key];
    }
    const value = key.split(".").reduce<unknown>(
        (current, part) => current && typeof current === "object"
            ? (current as Record<string, unknown>)[part]
            : undefined,
        messages[locale.value],
    );
    return String(value ?? key);
}

function parseAssignments(response: TransportResponse): AssignmentPage {
    const failure = responseFailure(response);
    if (failure) throw failure;
    const values = response.data?.assignments;
    if (!Array.isArray(values)) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    if (Array.isArray(response.data?.catalog)) {
        catalog.value = response.data.catalog.filter(
            (value): value is string => typeof value === "string",
        );
    }
    const items = values.filter((value): value is Assignment => {
        if (!value || typeof value !== "object") return false;
        const item = value as Record<string, unknown>;
        return typeof item.assignment_id === "string"
            && typeof item.grant_id === "string"
            && typeof item.github_user_id === "string"
            && typeof item.node === "string"
            && typeof item.effect === "string"
            && typeof item.priority === "number"
            && typeof item.grant_effect === "string";
    });
    const total = Number(response.data?.total ?? items.length);
    const limit = Number(response.data?.limit ?? pageSize);
    const offset = Number(response.data?.offset ?? 0);
    if (!Number.isInteger(total) || total < 0 || !Number.isInteger(limit)
        || limit < 1 || !Number.isInteger(offset) || offset < 0) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    return {items, total, limit, offset};
}

async function load() {
    permissionFailure.value = null;
    if (!workspace.value) return;
    try {
        await authorization.ensure(transport.selectedTeamId);
        if (!canRead.value) {
            permissionFailure.value = new RequestFailure(
                pageT("permission"),
                "forbidden",
                "permission_denied",
            );
            return;
        }
        await request.run(async (signal) => {
            const response = await transport.request({
                action: "read",
                node: node.value,
                data: {
                    limit: pageSize,
                    offset: (page.value - 1) * pageSize,
                    user_id_like: userFilter.value.trim(),
                    node_like: nodeFilter.value.trim(),
                    source: sourceFilter.value.trim(),
                    status: statusFilter.value || undefined,
                    expiry: expiryFilter.value || undefined,
                },
            });
            if (signal.aborted) {
                throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
            }
            return parseAssignments(response);
        }, {isEmpty: (result) => result.items.length === 0});
    } catch (cause) {
        if (cause instanceof RequestFailure && cause.code === "permission_denied") {
            permissionFailure.value = cause;
        }
    }
}

function sourceLabel(assignment: Assignment) {
    return assignment.source_type || assignment.source_id || baseT("common.emptyValue");
}

const filteredAssignments = computed(() => {
    return request.data.value?.items ?? [];
});

const groupedAssignments = computed(() => {
    const groups = new Map<string, Assignment[]>();
    for (const assignment of filteredAssignments.value) {
        const values = groups.get(assignment.github_user_id) ?? [];
        values.push(assignment);
        groups.set(assignment.github_user_id, values);
    }
    return [...groups.entries()].map(([userId, assignments]) => ({
        userId,
        assignments,
    }));
});

function effectLabel(value: string) {
    return value === "deny" ? pageT("deny") : pageT("allow");
}

function assignmentCountLabel(count: number) {
    return pageT("assignmentCount").replace("{count}", String(count));
}

function openEdit(assignment: Assignment) {
    if (!canManage.value || mutationPending.value) return;
    // 模板套用行的节点内容由模板决定，重写节点集合会让授权与模板脱钩。
    if (isTemplateOrigin(assignment)) return;
    selectedAssignment.value = assignment;
    editPermissions.value = Object.fromEntries(
        filteredAssignments.value
            .filter((item) => item.grant_id === assignment.grant_id)
            .map((item) => [item.node, item.effect === "deny" ? "deny" : "allow"]),
    );
    editExpiresAt.value = assignment.expires_at ?? null;
    mutationError.value = null;
    editOpen.value = true;
}

function isTemplateOrigin(assignment: Assignment) {
    return assignment.origin === "template";
}

// 分配写入按资源级委派节点做 require_grantable（后端 resource_grant_node）：只持有
// 业务节点但没拿到该资源 `.grant` 的账户，选中后提交必然失败，所以这里直接判定。
function canGrantNode(node: string) {
    const grantNode = resourceGrantNode(node);
    return authorization.can(grantNode) && authorization.canGrant(grantNode);
}

function isGrantable(assignment: Assignment) {
    return canGrantNode(assignment.node);
}

function closeEdit() {
    if (mutationPending.value) return;
    editOpen.value = false;
    selectedAssignment.value = null;
    editPermissions.value = {};
    editExpiresAt.value = null;
}

function openCreate() {
    if (!canManage.value || mutationPending.value) return;
    mutationError.value = null;
    createOpen.value = true;
}

function closeCreate() {
    if (mutationPending.value) return;
    createOpen.value = false;
}

function parsePermissions() {
    // 提交 {节点: effect}：deny 就是"阻止"，与 allow 同一份映射里表达。
    const result: Record<string, string> = {};
    for (const [node, effect] of Object.entries(editPermissions.value)) {
        result[node] = effect === "deny" ? "deny" : "allow";
    }
    return result;
}

function previewRemovedPermissions(response: TransportResponse) {
    const failure = responseFailure(response);
    if (failure) throw failure;
    const diff = response.data?.diff;
    if (!diff || typeof diff !== "object") {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    const diffRecord = diff as Record<string, unknown>;
    const removed = diffRecord.permissions_removed;
    if (!Array.isArray(removed) || !removed.every((value) => typeof value === "string")) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    if (typeof diffRecord.expiry_shortened !== "boolean") {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    return {
        removedPermissions: removed,
        expiryShortened: diffRecord.expiry_shortened,
    };
}

function mutationMessage(cause: unknown) {
    return cause instanceof Error ? cause.message : String(cause);
}

async function submitEdit() {
    if (!selectedAssignment.value || !canManage.value || mutationPending.value || !node.value) return;
    const assignment = selectedAssignment.value;
    const permissions = parsePermissions();
    const expiresAt = editExpiresAt.value;
    mutationPending.value = true;
    mutationError.value = null;
    try {
        const preview = await transport.request({
            action: "manage",
            node: node.value,
            data: {
                assignment_id: assignment.assignment_id,
                permissions,
                expires_at: expiresAt,
                preview: true,
            },
        });
        const previewDiff = previewRemovedPermissions(preview);
        let confirmationToken: string | undefined;
        if (previewDiff.removedPermissions.length > 0 || previewDiff.expiryShortened) {
            const confirmation = await transport.request({
                action: "manage",
                node: node.value,
                data: {
                    assignment_id: assignment.assignment_id,
                    mode: "confirm",
                },
            });
            const confirmationFailure = responseFailure(confirmation);
            if (confirmationFailure) throw confirmationFailure;
            const token = confirmation.data?.confirmation_token;
            if (typeof token !== "string" || !token) {
                throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
            }
            confirmationToken = token;
        }

        const data: Record<string, unknown> = {
            assignment_id: assignment.assignment_id,
            permissions,
            expires_at: expiresAt,
        };
        if (confirmationToken) data.confirmation_token = confirmationToken;
        const response = await transport.request({
            action: "manage",
            node: node.value,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data,
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        editOpen.value = false;
        selectedAssignment.value = null;
        editPermissions.value = {};
        editExpiresAt.value = null;
        toast.push(pageT("save"), "success");
        await load();
    } catch (cause) {
        mutationError.value = mutationMessage(cause);
        toast.push(mutationError.value, "error");
    } finally {
        mutationPending.value = false;
    }
}

async function createAssignment() {
    if (!canManage.value || mutationPending.value || !node.value) return;
    const userId = createUserId.value.trim();
    const permissions: Record<string, string> = {};
    for (const [node, effect] of Object.entries(createPermissions.value)) {
        const trimmed = node.trim();
        if (trimmed) permissions[trimmed] = effect === "deny" ? "deny" : "allow";
    }
    const expiresAt = createExpiresAt.value;
    if (!userId) {
        mutationError.value = pageT("invalid");
        toast.push(mutationError.value, "error");
        return;
    }

    mutationPending.value = true;
    mutationError.value = null;
    try {
        const response = await transport.request({
            action: "manage",
            node: node.value,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {
                user_id: userId,
                permissions,
                expires_at: expiresAt,
            },
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        createOpen.value = false;
        createUserId.value = "";
        createPermissions.value = {};
        createExpiresAt.value = null;
        toast.push(pageT("create"), "success");
        await load();
    } catch (cause) {
        mutationError.value = mutationMessage(cause);
        toast.push(mutationError.value, "error");
    } finally {
        mutationPending.value = false;
    }
}

type LifecycleMode = "suspend" | "restore" | "revoke" | "extend";

const extendOpen = ref(false);
const extendAssignment = ref<Assignment | null>(null);
const extendExpiresAt = ref<number | null>(null);
const lifecycleConfirmation = ref<{mode: "suspend" | "revoke"; assignment: Assignment} | null>(null);

function statusLabel(status?: string) {
    if (status === "expired") return pageT("expired");
    if (status === "suspended") return pageT("suspended");
    if (status === "revoked") return pageT("revoked");
    return pageT("active");
}

// 单行即一个 assignment 记录（一个 node）。
function lifecycleAssignmentNodes(): string[] {
    const pending = lifecycleConfirmation.value;
    return pending ? [pending.assignment.node] : [];
}

function openExtend(assignment: Assignment) {
    if (!canManage.value || mutationPending.value) return;
    extendAssignment.value = assignment;
    extendExpiresAt.value = assignment.expires_at ?? null;
    mutationError.value = null;
    extendOpen.value = true;
}

function closeExtend() {
    if (mutationPending.value) return;
    extendOpen.value = false;
    extendAssignment.value = null;
    extendExpiresAt.value = null;
}

async function runLifecycle(mode: LifecycleMode, assignment: Assignment) {
    if (!canManage.value || mutationPending.value || !node.value) return;
    if (mode === "extend") {
        openExtend(assignment);
        return;
    }
    if (mode === "suspend" || mode === "revoke") {
        // 破坏性操作先展示确认内容（目标、影响、后果），确认后再执行。
        lifecycleConfirmation.value = {mode, assignment};
        return;
    }

    await commitLifecycle(mode, assignment);
}

function closeLifecycleConfirmation() {
    if (!mutationPending.value) lifecycleConfirmation.value = null;
}

async function commitLifecycle(mode: LifecycleMode, assignment: Assignment) {
    if (!canManage.value || mutationPending.value || !node.value) return;

    mutationPending.value = true;
    mutationError.value = null;
    try {
        let confirmationToken: string | undefined;
        if (mode === "suspend" || mode === "revoke") {
            const confirmation = await transport.request({
                action: "manage",
                node: node.value,
                data: {
                    assignment_id: assignment.assignment_id,
                    mode: "confirm",
                    operation: mode,
                },
            });
            const confirmationFailure = responseFailure(confirmation);
            if (confirmationFailure) throw confirmationFailure;
            const token = confirmation.data?.confirmation_token;
            if (typeof token !== "string" || !token) {
                throw new RequestFailure(pageT("confirmInvalid"), "validation", "invalid_response");
            }
            confirmationToken = token;
        }

        const data: Record<string, unknown> = {
            assignment_id: assignment.assignment_id,
            mode,
        };
        if (confirmationToken) data.confirmation_token = confirmationToken;
        const response = await transport.request({
            action: "manage",
            node: node.value,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data,
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        lifecycleConfirmation.value = null;
        toast.push(pageT(mode), "success");
        await load();
    } catch (cause) {
        mutationError.value = mutationMessage(cause);
        toast.push(mutationError.value, "error");
    } finally {
        mutationPending.value = false;
    }
}

async function confirmLifecycle() {
    const pending = lifecycleConfirmation.value;
    if (!pending) return;
    await commitLifecycle(pending.mode, pending.assignment);
}

async function submitExtend() {
    const assignment = extendAssignment.value;
    if (!assignment || !canManage.value || mutationPending.value || !node.value) return;
    const expiresAt = extendExpiresAt.value;
    if (expiresAt === null) {
        mutationError.value = pageT("expiresAt");
        toast.push(mutationError.value, "error");
        return;
    }

    mutationPending.value = true;
    mutationError.value = null;
    try {
        const response = await transport.request({
            action: "manage",
            node: node.value,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {
                assignment_id: assignment.assignment_id,
                mode: "extend",
                expires_at: expiresAt,
            },
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        extendOpen.value = false;
        extendAssignment.value = null;
        extendExpiresAt.value = null;
        toast.push(pageT("extend"), "success");
        await load();
    } catch (cause) {
        mutationError.value = mutationMessage(cause);
        toast.push(mutationError.value, "error");
    } finally {
        mutationPending.value = false;
    }
}

function clearFilters() {
    userFilter.value = "";
    nodeFilter.value = "";
    sourceFilter.value = "";
    statusFilter.value = "";
    expiryFilter.value = "";
    page.value = 1;
}

function reloadFromFilter() {
    page.value = 1;
    void load();
}

function previousPage() {
    if (page.value > 1) {
        page.value -= 1;
        void load();
    }
}

function nextPage() {
    if (page.value < pageCount.value) {
        page.value += 1;
        void load();
    }
}

onMounted(load);
watch(() => workspace.value?.team_id, (current, previous) => {
    if (current === previous) return;
    clearFilters();
    void load();
});
watch([userFilter, nodeFilter, sourceFilter, statusFilter, expiryFilter], reloadFromFilter);
</script>

<template>
    <div class="assignments-page">
        <PageHeader
            :eyebrow="pageT('title')"
            :title="pageT('title')"
            :description="pageT('description')"
            :refresh-label="pageT('refresh')"
            :refreshing="request.loading.value"
            @refresh="load"
        >
            <template #actions>
                <UiButton
                    v-if="canManage"
                    variant="primary"
                    :disabled="mutationPending"
                    @click="openCreate"
                >
                    {{ pageT("create") }}
                </UiButton>
            </template>
        </PageHeader>

        <section class="filter-section" :aria-label="baseT('common.assignmentFilters')">
            <UiInput
                v-model="userFilter"
                :aria-label="pageT('searchUser')"
                :placeholder="pageT('searchUser')"
            />
            <UiInput
                v-model="nodeFilter"
                :aria-label="pageT('searchNode')"
                :placeholder="pageT('searchNode')"
            />
            <UiInput
                v-model="sourceFilter"
                :aria-label="pageT('searchSource')"
                :placeholder="pageT('searchSource')"
            />
            <select v-model="statusFilter" :aria-label="pageT('searchStatus')">
                <option value="">{{ pageT("allStatuses") }}</option>
                <option value="active">{{ pageT("active") }}</option>
                <option value="suspended">{{ pageT("suspended") }}</option>
                <option value="revoked">{{ pageT("revoked") }}</option>
                <option value="expired">{{ pageT("expired") }}</option>
            </select>
            <select v-model="expiryFilter" :aria-label="pageT('searchExpiry')">
                <option value="">{{ pageT("allExpiry") }}</option>
                <option value="expired">{{ pageT("expired") }}</option>
                <option value="permanent">{{ pageT("permanent") }}</option>
            </select>
            <UiButton
                v-if="userFilter || nodeFilter || sourceFilter || statusFilter || expiryFilter"
                variant="ghost"
                @click="clearFilters"
            >
                {{ pageT("clearFilters") }}
            </UiButton>
        </section>

        <RequestState
            :phase="phase"
            :message="!workspace ? pageT('noWorkspace') : phase === 'forbidden' ? pageT('permission') : null"
            @retry="load"
            @cancel="request.cancel"
        />

        <template v-if="request.data.value && phase !== 'forbidden'">
            <section v-for="group in groupedAssignments" :key="group.userId" class="group-section">
                <div class="group-heading">
                    <h3>{{ group.userId }}</h3>
                    <span>{{ assignmentCountLabel(group.assignments.length) }}</span>
                </div>
                <UiDataTable
                    :columns="[
                        {key: 'node', label: pageT('node')},
                        {key: 'source_id', label: pageT('source')},
                        {key: 'effect', label: pageT('effect')},
                        {key: 'priority', label: pageT('priority')},
                        {key: 'grant_effect', label: pageT('grant')},
                        {key: 'grantable', label: pageT('grantable')},
                        {key: 'status', label: pageT('status')},
                        {key: 'expires_at', label: pageT('expiresAt')},
                        {key: 'created_at', label: pageT('created')},
                        {key: 'grant_id', label: pageT('edit')},
                    ]"
                    :rows="group.assignments"
                >
                    <template #node="{row}">
                        <code>{{ row.node }}</code>
                    </template>
                    <template #source_id="{row}">
                        {{ sourceLabel(row) }}
                    </template>
                    <template #effect="{row}">
                        <span class="effect" :data-effect="row.effect">{{ effectLabel(row.effect) }}</span>
                    </template>
                    <template #grant_effect="{row}">
                        <span class="effect" :data-effect="row.grant_effect">{{ effectLabel(row.grant_effect) }}</span>
                    </template>
                    <template #grantable="{row}">
                        <span class="status" :data-status="isGrantable(row) ? 'active' : 'revoked'">
                            {{ isGrantable(row) ? pageT("allow") : pageT("deny") }}
                        </span>
                    </template>
                    <template #status="{row}">
                        <span class="status" :data-status="row.status ?? 'active'">
                            {{ statusLabel(row.status) }}
                        </span>
                    </template>
                    <template #expires_at="{row}">
                        {{ row.expires_at === null || row.expires_at === undefined
                            ? pageT("noExpiry")
                            : formatTimestamp(row.expires_at, localeName) }}
                    </template>
                    <template #created_at="{row}">
                        {{ formatTimestamp(row.created_at, localeName) }}
                    </template>
                    <template #grant_id="{row}">
                        <div class="row-actions">
                            <UiButton
                                v-if="canManage && row.status !== 'revoked'"
                                variant="ghost"
                                :disabled="mutationPending || isTemplateOrigin(row)"
                                :title="isTemplateOrigin(row) ? pageT('templateRowHint') : undefined"
                                @click="openEdit(row)"
                            >
                                {{ pageT("edit") }}
                            </UiButton>
                            <UiButton
                                v-if="canManage && row.status === 'active'"
                                variant="ghost"
                                :disabled="mutationPending"
                                @click="runLifecycle('suspend', row)"
                            >
                                {{ pageT("suspend") }}
                            </UiButton>
                            <UiButton
                                v-if="canManage && row.status === 'suspended'"
                                variant="ghost"
                                :disabled="mutationPending"
                                @click="runLifecycle('restore', row)"
                            >
                                {{ pageT("restore") }}
                            </UiButton>
                            <UiButton
                                v-if="canManage && row.status !== 'revoked'"
                                variant="ghost"
                                :disabled="mutationPending"
                                @click="runLifecycle('revoke', row)"
                            >
                                {{ pageT("revoke") }}
                            </UiButton>
                            <UiButton
                                v-if="canManage && row.status !== 'revoked'"
                                variant="ghost"
                                :disabled="mutationPending"
                                @click="runLifecycle('extend', row)"
                            >
                                {{ pageT("extend") }}
                            </UiButton>
                        </div>
                    </template>
                </UiDataTable>
            </section>
            <div v-if="pageCount > 1" class="pagination">
                <UiButton
                    variant="ghost"
                    :disabled="page === 1"
                    @click="previousPage"
                >
                    {{ pageT("previousPage") }}
                </UiButton>
                <span>{{ page }} / {{ pageCount }}</span>
                <UiButton
                    variant="ghost"
                    :disabled="page === pageCount"
                    @click="nextPage"
                >
                    {{ pageT("nextPage") }}
                </UiButton>
            </div>
        </template>

        <UiDialog :open="createOpen" :title="pageT('createTitle')" @close="closeCreate">
            <div class="assignment-form">
                <UiInput
                    v-model="createUserId"
                    :aria-label="pageT('userPlaceholder')"
                    :placeholder="pageT('userPlaceholder')"
                />
                <PermissionTreeEditor
                    v-model="createPermissions"
                    :effects-editable="true"
                    :grantable="canGrantNode"
                    :label="pageT('permissionPlaceholder')"
                    :options="editorCatalog"
                />
                <ValidityInput v-model="createExpiresAt" :label="pageT('expiresAt')"/>
                <p v-if="mutationError" class="mutation-error">{{ mutationError }}</p>
            </div>
            <template #footer>
                <UiButton variant="ghost" :disabled="mutationPending" @click="closeCreate">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton variant="primary" :disabled="mutationPending" @click="createAssignment">
                    {{ pageT("createConfirm") }}
                </UiButton>
            </template>
        </UiDialog>

        <UiDialog :open="editOpen" :title="pageT('editTitle')" @close="closeEdit">
            <dl v-if="selectedAssignment" class="details-list">
                <div><dt>{{ pageT("user") }}</dt><dd>{{ selectedAssignment.github_user_id }}</dd></div>
                <div><dt>{{ pageT("node") }}</dt><dd><code>{{ selectedAssignment.node }}</code></dd></div>
                <div><dt>{{ pageT("source") }}</dt><dd>{{ sourceLabel(selectedAssignment) }}</dd></div>
                <div><dt>{{ pageT("effect") }}</dt><dd>{{ effectLabel(selectedAssignment.effect) }}</dd></div>
            </dl>
            <PermissionTreeEditor
                v-model="editPermissions"
                :effects-editable="true"
                :grantable="canGrantNode"
                :label="pageT('permissionPlaceholder')"
                :options="editorCatalog"
            />
            <ValidityInput v-model="editExpiresAt" :label="pageT('expiresAt')"/>
            <dl v-if="editDiff" class="diff-panel" :aria-label="pageT('accessImpact')">
                <div>
                    <dt>{{ pageT("permissionsAdded") }}</dt>
                    <dd v-if="editDiff.added.length"><code v-for="item in editDiff.added" :key="item">{{ item }}</code></dd>
                    <dd v-else>{{ pageT("noChanges") }}</dd>
                </div>
                <div>
                    <dt>{{ pageT("permissionsRemoved") }}</dt>
                    <dd v-if="editDiff.removed.length"><code v-for="item in editDiff.removed" :key="item">{{ item }}</code></dd>
                    <dd v-else>{{ pageT("noChanges") }}</dd>
                </div>
                <div>
                    <dt>{{ pageT("expiryChanged") }}</dt>
                    <dd>{{ editDiff.expiryChanged ? pageT("yes") : pageT("no") }}</dd>
                </div>
                <div>
                    <dt>{{ pageT("effectChanged") }}</dt>
                    <dd>{{ editDiff.effectChanged ? pageT("yes") : pageT("no") }}</dd>
                </div>
                <div>
                    <dt>{{ pageT("priorityChanged") }}</dt>
                    <dd>{{ editDiff.priorityChanged ? pageT("yes") : pageT("no") }}</dd>
                </div>
                <div>
                    <dt>{{ pageT("affectedUsers") }}</dt>
                    <dd><code v-for="item in editDiff.affectedUsers" :key="item">{{ item }}</code></dd>
                </div>
                <div>
                    <dt>{{ pageT("accessImpact") }}</dt>
                    <dd>
                        {{ editDiff.accessImpact.addsAccess ? pageT("permissionsAdded") : "" }}
                        {{ editDiff.accessImpact.removesAccess ? pageT("permissionsRemoved") : "" }}
                        {{ editDiff.accessImpact.changesExpiry ? pageT("expiryChanged") : "" }}
                        {{ !editDiff.accessImpact.addsAccess && !editDiff.accessImpact.removesAccess && !editDiff.accessImpact.changesExpiry ? pageT("noChanges") : "" }}
                    </dd>
                </div>
            </dl>
            <p v-if="mutationError" class="mutation-error">{{ mutationError }}</p>
            <template #footer>
                <UiButton variant="ghost" :disabled="mutationPending" @click="closeEdit">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton variant="primary" :disabled="mutationPending" @click="submitEdit">
                    {{ pageT("save") }}
                </UiButton>
            </template>
        </UiDialog>
        <UiDialog :open="extendOpen" :title="pageT('extendTitle')" @close="closeExtend">
            <div class="extend-form">
                <p v-if="extendAssignment">
                    <code>{{ extendAssignment.assignment_id }}</code>
                </p>
                <ValidityInput v-model="extendExpiresAt" :label="pageT('expiresAt')"/>
                <p v-if="mutationError" class="mutation-error">{{ mutationError }}</p>
            </div>
            <template #footer>
                <UiButton variant="ghost" :disabled="mutationPending" @click="closeExtend">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton variant="primary" :disabled="mutationPending" @click="submitExtend">
                    {{ pageT("extend") }}
                </UiButton>
            </template>
        </UiDialog>
        <UiDialog
            :open="Boolean(lifecycleConfirmation)"
            :title="pageT('lifecycleTitle')"
            @close="closeLifecycleConfirmation"
        >
            <div v-if="lifecycleConfirmation" class="lifecycle-form">
                <p>{{ pageT("lifecycleBody") }}</p>
                <dl class="details-list">
                    <div><dt>{{ pageT("lifecycleUser") }}</dt><dd>{{ lifecycleConfirmation.assignment.github_user_id }}</dd></div>
                    <div><dt>{{ pageT("lifecycleId") }}</dt><dd><code>{{ lifecycleConfirmation.assignment.assignment_id }}</code></dd></div>
                    <div><dt>{{ pageT("lifecycleStatus") }}</dt><dd>{{ statusLabel(lifecycleConfirmation.assignment.status) }}</dd></div>
                    <div><dt>{{ pageT("lifecycleAction") }}</dt><dd>{{ pageT(lifecycleConfirmation.mode) }}</dd></div>
                    <div>
                        <dt>{{ pageT("lifecycleNodes") }}</dt>
                        <dd>
                            <ul v-if="lifecycleAssignmentNodes().length" class="node-list">
                                <li v-for="item in lifecycleAssignmentNodes()" :key="item"><code>{{ item }}</code></li>
                            </ul>
                            <span v-else>{{ pageT("noNodes") }}</span>
                        </dd>
                    </div>
                    <div><dt>{{ pageT("lifecycleImpact") }}</dt><dd>{{ pageT("yes") }}</dd></div>
                </dl>
                <p class="lifecycle-effect">
                    {{ lifecycleConfirmation.mode === "revoke"
                        ? pageT("lifecycleRevoke")
                        : pageT("lifecycleSuspend") }}
                </p>
                <p v-if="mutationError" class="mutation-error">{{ mutationError }}</p>
            </div>
            <template #footer>
                <UiButton variant="ghost" :disabled="mutationPending" @click="closeLifecycleConfirmation">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton variant="primary" :disabled="mutationPending" @click="confirmLifecycle">
                    {{ pageT("lifecycleConfirm") }}
                </UiButton>
            </template>
        </UiDialog>
    </div>
</template>

<style scoped>
.assignments-page {
    display: grid;
    gap: 24px;
}

.filter-section {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr)) auto;
    gap: 10px;
    align-items: center;
}

.group-section {
    min-width: 0;
}

.group-heading {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 10px;
}

.group-heading h3 {
    margin: 0;
}

.group-heading span {
    color: #9aa6b2;
    font-size: 12px;
}

code {
    color: #c8d5ff;
}

.effect[data-effect="allow"] {
    color: #78d88c;
}

.effect[data-effect="deny"] {
    color: #ff9b9b;
}

.details-list {
    display: grid;
    gap: 12px;
    margin: 0;
}

.details-list div {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    padding-bottom: 10px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.details-list dt {
    color: #9aa6b2;
}

.details-list dd {
    margin: 0;
    text-align: right;
}

.diff-panel {
    display: grid;
    gap: 10px;
    margin: 14px 0;
    padding: 12px;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.04);
}

.diff-panel div {
    display: grid;
    grid-template-columns: 140px minmax(0, 1fr);
    gap: 12px;
}

.diff-panel dt {
    color: #9aa6b2;
}

.diff-panel dd {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    min-width: 0;
    margin: 0;
}

.diff-panel code {
    overflow-wrap: anywhere;
}

textarea {
    width: 100%;
    min-height: 140px;
    padding: 10px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    resize: vertical;
    background: #101216;
    color: #edf1f7;
}

.mutation-error {
    color: #ff9b9b;
}

select {
    min-height: 40px;
    padding: 0 10px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    background: #101216;
    color: #edf1f7;
}

.row-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}

.pagination {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 12px;
    color: #9aa6b2;
}

.assignment-form,
.extend-form,
.lifecycle-form {
    display: grid;
    gap: 12px;
}

.assignment-form p,
.extend-form p,
.lifecycle-form p {
    margin: 0;
}

.lifecycle-effect {
    padding: 10px 12px;
    border: 1px solid rgba(255, 155, 155, 0.35);
    border-radius: 8px;
    background: rgba(255, 155, 155, 0.08);
    color: #ffb3b3;
}

.node-list {
    display: grid;
    gap: 4px;
    margin: 0;
    padding: 0;
    list-style: none;
}

.status[data-status="active"] {
    color: #78d88c;
}

.status[data-status="suspended"] {
    color: #ffcb73;
}

.status[data-status="revoked"] {
    color: #ff9b9b;
}

@media (max-width: 680px) {
    .filter-section {
        grid-template-columns: 1fr;
    }

    .group-heading {
        align-items: flex-start;
        flex-direction: column;
    }
}
</style>
