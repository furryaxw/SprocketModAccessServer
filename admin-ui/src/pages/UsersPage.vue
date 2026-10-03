<script lang="ts" setup>
import {computed, onBeforeUnmount, onMounted, ref, watch} from "vue";
import {transport, type TransportResponse} from "../api/transport";
import {responseFailure, RequestFailure, type RequestPhase} from "../composables/requestState";
import {useAuthorization} from "../composables/useAuthorization";
import {useRequest} from "../composables/useRequest";
import {useWorkspaceContext} from "../composables/useWorkspaceContext";
import {useSession} from "../composables/useSession";
import {formatTimestamp} from "../composables/time";
import UiDataTable from "../components/UiDataTable.vue";
import RequestState from "../components/RequestState.vue";
import {useLocale} from "../i18n";
import UiDialog from "../components/UiDialog.vue";
import UiButton from "../components/UiButton.vue";
import PageHeader from "../components/PageHeader.vue";
import {useToast} from "../composables/useToast";
import UiInput from "../components/UiInput.vue";
import PermissionTreeEditor from "../components/PermissionTreeEditor.vue";

type User = {
    github_user_id: string;
    login_snapshot?: string;
    display_name?: string;
    status?: string;
    created_at?: number;
    updated_at?: number;
    last_login_at?: number | null;
    source_type?: string;
    source_id?: string;
    joined_at?: number;
    // 以下键只用于 UiDataTable 的列投影，不来自后端数据。
    identity?: string;
    actions?: string;
    _select?: string;
};

type UserPage = {
    items: User[];
    total: number;
    limit: number;
    offset: number;
};

type TemplateOption = {
    template_id: string;
    name: string;
};

type PermissionRow = {
    assignment_id?: string;
    node?: string;
    value?: string;
    effect?: string;
    source_type?: string;
    source_id?: string;
    priority?: number;
    expires_at?: number | string | null;
};

const messages = {
    zh: {
        title: "用户",
        description: "查看当前工作区可见的用户，并管理状态与权限模板。",
        refresh: "刷新",
        scope: "范围",
        totalUsers: "{count} 个用户",
        totalMembers: "{count} 名成员",
        filtered: "已应用过滤",
        updatedAt: "数据更新于",
        invite: "邀请成员",
        inviteTitle: "邀请 Team 成员",
        inviteUser: "GitHub 用户 ID",
        inviteTemplate: "权限模板",
        sendInvite: "发送邀请",
        inviteSent: "{name} 的邀请已创建。",
        inviteTokenHint: "把这段邀请码交给被邀请人，由其在上方账户菜单里输入接受。",
        inviteTokenExpires: "邀请码有效期至",
        copy: "复制",
        copied: "已复制",
        identity: "用户",
        status: "状态",
        source: "权限来源",
        joined: "加入时间",
        created: "创建时间",
        lastLogin: "最近登录",
        actions: "操作",
        active: "正常",
        suspended: "已暂停",
        suspend: "暂停",
        activate: "启用",
        nodes: "权限节点",
        nodesTitle: "设置系统权限节点",
        nodesHint: "直接增删该用户的系统级节点（system.*、team.system.* 与 *）。",
        nodesSaved: "{name} 的系统权限节点已更新。",
        nodesSave: "保存节点",
        templateFreeText: "模板名称",
        templateUnavailable: "没有读取权限模板的权限，请直接输入模板名称。",
        confirmStatusTitle: "确认用户状态变更",
        confirmStatus: "确认变更",
        statusChanged: "{name}：{action}已完成。",
        details: "详情",
        edit: "编辑",
        save: "保存",
        detailsTitle: "用户详情",
        permissions: "系统权限",
        permissionsEmpty: "该用户没有显式系统权限。",
        assignmentSource: "来源",
        assignmentPriority: "优先级",
        assignmentExpiry: "有效期",
        allow: "允许",
        deny: "拒绝",
        searchGithubLogin: "按 GitHub 登录名过滤",
        searchDisplayName: "按显示名过滤",
        searchUserId: "按用户 ID 过滤",
        clearFilters: "清除过滤器",
        filterNoMatch: "没有匹配的用户。",
        select: "选择",
        selectedCount: "已选 {count}",
        clearSelection: "清除选择",
        batchSuspend: "批量暂停",
        batchActivate: "批量启用",
        batchConfirmTitle: "确认批量状态变更",
        batchConfirmBody: "{action} {count} 个用户：",
        batchComplete: "已更新 {count} 个用户。",
        batchPartial: "已更新 {done}/{total} 个用户后停止：{message}",
        readOnlyNotice: "你可以修改自己的状态和权限；管理其他用户需要 user.manage 或 system.users.manage。",
        lockedHint: "需要 user.manage、user.<id>.manage 或 system.users.manage 才能修改该用户。",
        memberDetailsTitle: "成员详情",
        memberEditTitle: "变更成员权限模板",
        memberTemplate: "权限模板",
        memberManaged: "{name} 的权限模板已更新为 {template}。",
        memberRemove: "移除成员",
        memberRemoveTitle: "确认移除成员",
        memberRemoveBody: "{name} 将离开该 Team，其 Team 权限与来源一并撤销。",
        memberRemoveConfirm: "确认移除",
        memberNodesDenied: "没有查看该成员权限的权限。",
        memberNodesEmpty: "该成员没有 Team 权限记录。",
        ownerTemplateNote: "Team Owner 的模板只能是 Owner 或 Admin。",
        selfStatusHint: "不能变更自己账户的状态：暂停后没有恢复入口。",
        systemAccountHint: "内置 system 账户是保留身份，状态不可变更。",
        batchNoneHint: "所选账户里没有可执行该操作的账户。",
        previousPage: "上一页",
        nextPage: "下一页",
        pageLabel: "第 {page} / {count} 页",
        noPermission: "没有查看用户的权限。",
        noWorkspace: "当前没有可用工作区。",
        invalid: "用户响应格式无效。",
        close: "关闭",
    },
    en: {
        title: "Users",
        description: "Review visible users in this workspace and manage their status and permission template.",
        refresh: "Refresh",
        scope: "Scope",
        totalUsers: "{count} users",
        totalMembers: "{count} members",
        filtered: "Filtered",
        updatedAt: "Data loaded at",
        invite: "Invite member",
        inviteTitle: "Invite Team member",
        inviteUser: "GitHub user ID",
        inviteTemplate: "Permission template",
        sendInvite: "Send invite",
        inviteSent: "Invitation created for {name}.",
        inviteTokenHint: "Hand this invitation code to the invited account and let it accept from the account menu.",
        inviteTokenExpires: "Invitation code valid until",
        copy: "Copy",
        copied: "Copied",
        identity: "User",
        status: "Status",
        source: "Permission source",
        joined: "Joined",
        created: "Created",
        lastLogin: "Last login",
        actions: "Actions",
        active: "Active",
        suspended: "Suspended",
        suspend: "Suspend",
        activate: "Activate",
        nodes: "Permission nodes",
        nodesTitle: "Set system permission nodes",
        nodesHint: "Add or remove this user's system-level nodes (system.*, team.system.* and *).",
        nodesSaved: "{name} system permission nodes updated.",
        nodesSave: "Save nodes",
        templateFreeText: "Template name",
        templateUnavailable: "You cannot list permission templates here; type the template name instead.",
        confirmStatusTitle: "Confirm user status change",
        confirmStatus: "Confirm",
        statusChanged: "{name}: {action} applied.",
        details: "Details",
        edit: "Edit",
        save: "Save",
        detailsTitle: "User details",
        permissions: "System permissions",
        permissionsEmpty: "This user has no explicit system permissions.",
        assignmentSource: "Source",
        assignmentPriority: "Priority",
        assignmentExpiry: "Expiry",
        allow: "Allow",
        deny: "Deny",
        searchGithubLogin: "Filter by GitHub login",
        searchDisplayName: "Filter by display name",
        searchUserId: "Filter by stable user ID",
        clearFilters: "Clear filters",
        filterNoMatch: "No user matches the filters.",
        select: "Select",
        selectedCount: "{count} selected",
        clearSelection: "Clear selection",
        batchSuspend: "Suspend selected",
        batchActivate: "Activate selected",
        batchConfirmTitle: "Confirm batch status change",
        batchConfirmBody: "{action} {count} users:",
        batchComplete: "{count} users updated.",
        batchPartial: "Stopped after {done}/{total} users: {message}",
        readOnlyNotice: "You can manage your own status and permissions; managing other users requires user.manage or system.users.manage.",
        lockedHint: "Managing this user requires user.manage, user.<id>.manage, or system.users.manage.",
        memberDetailsTitle: "Member details",
        memberEditTitle: "Change member template",
        memberTemplate: "Permission template",
        memberManaged: "Template for {name} updated to {template}.",
        memberRemove: "Remove member",
        memberRemoveTitle: "Confirm member removal",
        memberRemoveBody: "{name} leaves this Team and its Team permissions and source are revoked.",
        memberRemoveConfirm: "Remove",
        memberNodesDenied: "You cannot read this member's permissions.",
        memberNodesEmpty: "This member has no Team permission records.",
        ownerTemplateNote: "A Team Owner keeps the Owner or Admin template.",
        selfStatusHint: "Your own account status cannot change: suspending it leaves no way back.",
        systemAccountHint: "The built-in system account is a reserved identity; its status cannot change.",
        batchNoneHint: "No selected account can run this action.",
        previousPage: "Previous page",
        nextPage: "Next page",
        pageLabel: "Page {page} / {count}",
        noPermission: "You do not have permission to view users.",
        noWorkspace: "No workspace is available.",
        invalid: "The users response is invalid.",
        close: "Close",
    },
} as const;

const {locale, t: baseT} = useLocale();
const authorization = useAuthorization();
const {selected} = useWorkspaceContext();
const {user: sessionUser} = useSession();
const toast = useToast();

const request = useRequest<UserPage>();
const detailRequest = useRequest<User>();
const permissionRequest = useRequest<PermissionRow[]>();
const templateRequest = useRequest<TemplateOption[]>();
const nodeCatalogRequest = useRequest<string[]>();
const userNodeRequest = useRequest<PermissionRow[]>();

const permissionFailure = ref<RequestFailure | null>(null);
const detailOpen = ref(false);
const selectedUser = ref<User | null>(null);
const nodeDialogOpen = ref(false);
const nodeUser = ref<User | null>(null);
// 单一映射：{节点: effect}，deny 就是“阻止”。
const nodePermissions = ref<Record<string, string>>({});
// 由权限模板提供的系统级节点：读取时能看到，保存时不写进直属集合（写入不生效，
// 而且会把模板来源降级成 direct）。
const templateNodeValues = ref<string[]>([]);
const nodeCatalog = ref<string[]>([]);
const inviteDialogOpen = ref(false);
const inviteUserId = ref("");
const inviteTemplateName = ref("");
const inviteToken = ref("");
const inviteExpiresAt = ref<number | null>(null);
const statusConfirmation = ref<{user: User; action: "suspend" | "activate"} | null>(null);
const batchConfirmation = ref<{action: "suspend" | "activate"; users: User[]} | null>(null);
const selectedUserIds = ref<string[]>([]);
const loginFilter = ref("");
const displayNameFilter = ref("");
const userIdFilter = ref("");
const page = ref(1);
const pageSize = 50;
const lastLoadedAt = ref<Date | null>(null);
const mutationPending = ref(false);
let filterTimer: number | null = null;

const localeName = computed(() => locale.value === "zh" ? "zh-CN" : "en-US");
const workspace = computed(() => selected.value);
const workspaceLabel = computed(() => workspace.value?.name ?? baseT("common.emptyValue"));
const isSystemWorkspace = computed(() => workspace.value?.workspace_kind === "system");
const listNode = computed(() => isSystemWorkspace.value
    ? "system.users"
    : workspace.value ? `team.${workspace.value.team_id}.users` : "");
const templateNode = computed(() => !workspace.value
    ? ""
    : isSystemWorkspace.value
        ? "team.system.permission_templates"
        : `team.${workspace.value.team_id}.permission_templates`);
const currentUserId = computed(() => String(sessionUser.value?.github_user_id ?? ""));

const canRead = computed(() =>
    Boolean(listNode.value) && authorization.can(`${listNode.value}.read`),
);
const canInvite = computed(() =>
    !isSystemWorkspace.value
    && Boolean(listNode.value)
    && authorization.can(`${listNode.value}.invite`),
);
const canReadTemplates = computed(() =>
    Boolean(templateNode.value) && authorization.can(`${templateNode.value}.read`),
);
// 后端对用户状态与权限模板的修改实际走 manage 路径：本人 → user.manage →
// user.<id>.manage → system.users.manage（users/resources.py:_require_user_permission），
// 细粒度节点 suspend/activate/set_permission_template 不被任何处理器检查。
const managesOthers = computed(() =>
    authorization.can("user.manage") || authorization.can("system.users.manage"),
);
const canBatch = computed(() =>
    isSystemWorkspace.value && managesOthers.value && canRead.value,
);
const hasFilters = computed(() =>
    Boolean(loginFilter.value.trim() || displayNameFilter.value.trim() || userIdFilter.value.trim()),
);

function managesUser(user: User) {
    return user.github_user_id === currentUserId.value
        || managesOthers.value
        || authorization.can(`user.${user.github_user_id}.manage`);
}

// 用户管理处理器强制 System Team 上下文（require_context_team），
// Team 工作区只读成员目录 + 邀请。
function canManageUser(user: User) {
    return isSystemWorkspace.value && managesUser(user);
}

// 状态变更的目标保护与后端一致（users/resources.py:_guard_status_target）。
function statusLockReason(user: User): string {
    if (user.github_user_id === currentUserId.value) return pageT("selfStatusHint");
    if (user.github_user_id === "system") return pageT("systemAccountHint");
    return "";
}

// 全局门按被调用的节点判定（presentation/authorization.py），所以状态按钮按实际节点开门。
const canSuspendUser = computed(() => authorization.can("system.users.suspend"));
const canActivateUser = computed(() => authorization.can("system.users.activate"));

function batchTargets(action: "suspend" | "activate") {
    if (action === "suspend" ? !canSuspendUser.value : !canActivateUser.value) return [];
    return selectedUsers.value.filter((user) =>
        canManageUser(user)
        && !statusLockReason(user)
        && (action === "suspend" ? user.status === "active" : user.status === "suspended"),
    );
}

const users = computed(() => request.data.value?.items ?? []);
const totalUsers = computed(() => request.data.value?.total ?? 0);
const pageCount = computed(() => Math.max(1, Math.ceil(totalUsers.value / pageSize)));
const selectedUsers = computed(() => users.value.filter((user) => selectedUserIds.value.includes(user.github_user_id)));
const phase = computed<RequestPhase>(() => {
    if (!workspace.value) return "empty";
    if (permissionFailure.value) return "forbidden";
    if (authorization.phase.value !== "success") return authorization.phase.value;
    if (!canRead.value) return "forbidden";
    return request.phase.value;
});
const emptyMessage = computed(() =>
    hasFilters.value ? pageT("filterNoMatch") : null,
);
const columns = computed<Array<{key: keyof User; label: string}>>(() => {
    const values: Array<{key: keyof User; label: string}> = [];
    if (canBatch.value) {
        values.push({key: "_select", label: pageT("select")});
    }
    values.push({key: "identity", label: pageT("identity")});
    values.push({key: "status", label: pageT("status")});
    if (isSystemWorkspace.value) {
        values.push({key: "created_at", label: pageT("created")});
        values.push({key: "last_login_at", label: pageT("lastLogin")});
        values.push({key: "actions", label: pageT("actions")});
    } else {
        values.push({key: "source_id", label: pageT("source")});
        values.push({key: "joined_at", label: pageT("joined")});
        values.push({key: "actions", label: pageT("actions")});
    }
    return values;
});
const templates = computed(() => templateRequest.data.value ?? []);
const templateLoadFailed = computed(() =>
    templateRequest.phase.value === "error"
    || templateRequest.phase.value === "server-error"
    || templateRequest.phase.value === "validation",
);
// 编辑器只渲染后端给的目录，不在这里做任何过滤/拼接——否则会和其它页面的权限树不一致。
const nodeOptions = computed(() => nodeCatalog.value);
const nodeLoadFailed = computed(() =>
    userNodeRequest.phase.value === "error"
    || userNodeRequest.phase.value === "server-error"
    || userNodeRequest.phase.value === "validation"
    || nodeCatalogRequest.phase.value === "error"
    || nodeCatalogRequest.phase.value === "server-error"
    || nodeCatalogRequest.phase.value === "validation",
);

function pageT(key: string): string {
    const value = key.split(".").reduce<unknown>(
        (current, part) => current && typeof current === "object"
            ? (current as Record<string, unknown>)[part]
            : undefined,
        messages[locale.value],
    );
    return String(value ?? key);
}

function pageTFormat(key: string, values: Record<string, string | number>): string {
    return Object.entries(values).reduce(
        (text, [name, value]) => text.replace(`{${name}}`, String(value)),
        pageT(key),
    );
}

function displayName(user: User) {
    return user.display_name || user.login_snapshot || user.github_user_id;
}

function statusLabel(status?: string) {
    return status === "suspended" ? pageT("suspended") : pageT("active");
}

function permissionEffectLabel(effect: string) {
    if (effect === "allow") return pageT("allow");
    if (effect === "deny") return pageT("deny");
    return effect;
}

function actionLabel(action: "suspend" | "activate") {
    return action === "suspend" ? pageT("suspend") : pageT("activate");
}

function isSelected(user: User) {
    return selectedUserIds.value.includes(user.github_user_id);
}

function toggleSelected(user: User, checked: boolean) {
    const values = new Set(selectedUserIds.value);
    if (checked) {
        values.add(user.github_user_id);
    } else {
        values.delete(user.github_user_id);
    }
    selectedUserIds.value = [...values];
}

function clearSelection() {
    selectedUserIds.value = [];
}

function parseUsers(response: TransportResponse): UserPage {
    const failure = responseFailure(response);
    if (failure) throw failure;
    const values = response.data?.[isSystemWorkspace.value ? "users" : "members"];
    if (!Array.isArray(values)) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    const items = values.filter((value): value is User =>
        Boolean(value)
        && typeof value === "object"
        && typeof (value as Record<string, unknown>).github_user_id === "string",
    );
    const total = Number(response.data?.total ?? items.length);
    const limit = Number(response.data?.limit ?? pageSize);
    const offset = Number(response.data?.offset ?? 0);
    if (!Number.isInteger(total) || total < 0 || !Number.isInteger(limit) || limit < 1
        || !Number.isInteger(offset) || offset < 0) {
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
            permissionFailure.value = new RequestFailure(pageT("noPermission"), "forbidden", "permission_denied");
            return;
        }
        await request.run(async (signal) => {
            const response = await transport.request({
                action: "read",
                node: listNode.value,
                data: {
                    limit: pageSize,
                    offset: (page.value - 1) * pageSize,
                    login: loginFilter.value.trim(),
                    display_name: displayNameFilter.value.trim(),
                    user_id: userIdFilter.value.trim(),
                },
            });
            if (signal.aborted) {
                throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
            }
            return parseUsers(response);
        }, {isEmpty: (result) => result.items.length === 0});
        lastLoadedAt.value = new Date();
        selectedUserIds.value = selectedUserIds.value.filter((id) =>
            users.value.some((user) => user.github_user_id === id && canManageUser(user)),
        );
    } catch (cause) {
        if (cause instanceof RequestFailure && cause.code === "permission_denied") {
            permissionFailure.value = cause;
        }
    }
}

function scheduleFilterLoad() {
    page.value = 1;
    if (filterTimer !== null) window.clearTimeout(filterTimer);
    filterTimer = window.setTimeout(() => {
        filterTimer = null;
        void load();
    }, 300);
}

function clearFilters() {
    loginFilter.value = "";
    displayNameFilter.value = "";
    userIdFilter.value = "";
}

function previousPage() {
    if (page.value === 1) return;
    page.value -= 1;
    void load();
}

function nextPage() {
    if (page.value >= pageCount.value) return;
    page.value += 1;
    void load();
}

async function ensureTemplates() {
    if (!canReadTemplates.value || templateRequest.data.value !== null || templateRequest.loading.value) {
        return;
    }
    try {
        await templateRequest.run(async (signal) => {
            const response = await transport.request({
                action: "read",
                node: templateNode.value,
                data: {limit: 100, offset: 0},
            });
            if (signal.aborted) {
                throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
            }
            const failure = responseFailure(response);
            if (failure) throw failure;
            const values = response.data?.templates;
            if (!Array.isArray(values)) {
                throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
            }
            return values
                .filter((value): value is Record<string, unknown> =>
                    Boolean(value)
                    && typeof value === "object"
                    && typeof (value as Record<string, unknown>).template_id === "string"
                    && typeof (value as Record<string, unknown>).name === "string"
                    && (value as Record<string, unknown>).status !== "disabled",
                )
                .map((value) => ({
                    template_id: String(value.template_id),
                    name: String(value.name),
                }))
                .sort((left, right) => left.name.localeCompare(right.name));
        });
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    }
}

async function loadDetail(userId: string) {
    if (!userId) {
        toast.push(pageT("invalid"), "error");
        return;
    }
    await detailRequest.run(async (signal) => {
        const response = await transport.request({
            action: "read_user",
            node: "system.users",
            data: {user_id: userId},
        });
        if (signal.aborted) {
            throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
        }
        const failure = responseFailure(response);
        if (failure) throw failure;
        if (!response.data || typeof response.data.github_user_id !== "string") {
            throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
        }
        return response.data as unknown as User;
    });
}

async function loadPermissions(userId: string) {
    await permissionRequest.run(async (signal) => {
        const response = await transport.request({
            action: "read",
            node: "system.users.permissions",
            data: {user_id: userId},
        });
        if (signal.aborted) {
            throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
        }
        const failure = responseFailure(response);
        if (failure) throw failure;
        const values = response.data?.permissions;
        const assignments = response.data?.assignments;
        if (!Array.isArray(values) || !Array.isArray(assignments)) {
            throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
        }
        const permissionValues = values.filter((value): value is PermissionRow =>
            Boolean(value) && typeof value === "object"
            && typeof (value as Record<string, unknown>).value === "string",
        );
        const assignmentValues = assignments.filter((value): value is PermissionRow =>
            Boolean(value) && typeof value === "object"
            && typeof (value as Record<string, unknown>).node === "string",
        );
        return [...permissionValues, ...assignmentValues];
    });
}

async function openDetails(user: User) {
    if (!isSystemWorkspace.value || !canRead.value) return;
    selectedUser.value = user;
    detailOpen.value = true;
    detailRequest.reset();
    permissionRequest.reset();
    const userId = user.github_user_id.trim();
    await Promise.all([loadDetail(userId), loadPermissions(userId)]);
}

function closeDetails() {
    detailOpen.value = false;
    selectedUser.value = null;
    detailRequest.reset();
    permissionRequest.reset();
}

function retryDetail() {
    const user = selectedUser.value;
    if (user) void loadDetail(user.github_user_id.trim());
}

function retryPermissions() {
    const user = selectedUser.value;
    if (user) void loadPermissions(user.github_user_id.trim());
}

async function ensureNodeCatalog() {
    if (nodeCatalog.value.length || nodeCatalogRequest.data.value !== null) return;
    await nodeCatalogRequest.run(async (signal) => {
        const response = await transport.request({
            action: "read",
            node: "system.schema.permissions",
        });
        if (signal.aborted) {
            throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
        }
        const failure = responseFailure(response);
        if (failure) throw failure;
        const values = response.data?.permissions;
        if (!Array.isArray(values) || !values.every((value) => typeof value === "string")) {
            throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
        }
        return [...values] as string[];
    });
    nodeCatalog.value = nodeCatalogRequest.data.value ?? [];
}

async function loadUserNodes(userId: string) {
    await userNodeRequest.run(async (signal) => {
        const response = await transport.request({
            action: "read",
            node: "system.users.permissions",
            data: {user_id: userId},
        });
        if (signal.aborted) {
            throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
        }
        const failure = responseFailure(response);
        if (failure) throw failure;
        const values = response.data?.permissions;
        if (!Array.isArray(values)) {
            throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
        }
        const rows = values.filter((value): value is PermissionRow =>
            Boolean(value) && typeof value === "object"
            && typeof (value as Record<string, unknown>).value === "string",
        );
        // effect 由后端读取结果带回；保存时按节点回填，不会把已有 deny 改成 allow。
        nodePermissions.value = Object.fromEntries(rows.map((row) => [
            String(row.value),
            String(row.effect ?? "allow") === "deny" ? "deny" : "allow",
        ]));
        templateNodeValues.value = rows
            .filter((row) => row.source_type === "permission_template")
            .map((row) => String(row.value));
        return rows;
    });
}

async function openNodeEditor(user: User) {
    if (!isSystemWorkspace.value || !canManageUser(user) || mutationPending.value) return;
    nodeUser.value = user;
    nodePermissions.value = {};
    nodeDialogOpen.value = true;
    userNodeRequest.reset();
    await Promise.all([ensureNodeCatalog(), loadUserNodes(user.github_user_id)]);
}

function closeNodeEditor() {
    if (mutationPending.value) return;
    nodeDialogOpen.value = false;
    nodeUser.value = null;
    userNodeRequest.reset();
}

async function saveUserNodes() {
    const user = nodeUser.value;
    if (!user || !canManageUser(user) || mutationPending.value) return;
    const permissions: Record<string, string> = {};
    for (const [node, effect] of Object.entries(nodePermissions.value)) {
        // 模板提供的节点不进直属集合：写入不会改变它们，只会让来源看起来是 direct。
        if (templateNodeValues.value.includes(node)) continue;
        permissions[node] = effect === "deny" ? "deny" : "allow";
    }
    mutationPending.value = true;
    try {
        // 后端 set_user_system_permissions 是整体替换语义：提交集合即最终集合。
        // 模板实例在后端树里就是节点，提交模板节点即可，由后端在求值时展开。
        const response = await transport.request({
            action: "manage",
            node: "system.users.permissions",
            data: {
                user_id: user.github_user_id,
                permissions,
            },
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        nodeDialogOpen.value = false;
        nodeUser.value = null;
        toast.push(pageTFormat("nodesSaved", {name: displayName(user)}), "success");
        await load();
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
        mutationPending.value = false;
    }
}

function openInvite() {
    if (!canInvite.value || mutationPending.value) return;
    inviteDialogOpen.value = true;
    inviteUserId.value = "";
    inviteTemplateName.value = "";
    inviteToken.value = "";
    inviteExpiresAt.value = null;
    void ensureTemplates();
}

// ---- Team 工作区：成员详情 / 变更模板 / 移除 ----
const memberDetails = ref<User | null>(null);
const memberNodes = ref<PermissionRow[]>([]);
const memberNodesRequest = useRequest<PermissionRow[]>();
const memberEdit = ref<User | null>(null);
const memberTemplateName = ref("");
const memberRemoveConfirm = ref<User | null>(null);

const canManageMembers = computed(() =>
    Boolean(listNode.value) && !isSystemWorkspace.value && authorization.can(`${listNode.value}.manage`),
);
const canRemoveMembers = computed(() =>
    Boolean(listNode.value) && !isSystemWorkspace.value && authorization.can(`${listNode.value}.remove`),
);
const canReadMemberNodes = computed(() =>
    Boolean(assignmentNode.value) && authorization.can(`${assignmentNode.value}.read`),
);
const assignmentNode = computed(() => workspace.value
    ? `team.${workspace.value.team_id}.permission_assignments`
    : "");
// Team Owner 的模板只能停留在 Owner/Admin（后端 policy），移除则完全不允许。
function isTeamOwner(row: User) {
    return Boolean(workspace.value && row.github_user_id === workspace.value.owner_user_id);
}
const memberTemplateOptions = computed(() => {
    const target = memberEdit.value;
    if (target && isTeamOwner(target)) {
        return templates.value.filter((item) => ["owner", "admin"].includes(item.name.toLowerCase()));
    }
    return templates.value;
});

async function openMemberDetails(row: User) {
    memberDetails.value = row;
    memberNodes.value = [];
    memberNodesRequest.reset();
    if (!canReadMemberNodes.value || !assignmentNode.value) return;
    try {
        await memberNodesRequest.run(async (signal) => {
            const response = await transport.request({
                action: "read",
                node: assignmentNode.value,
                data: {user_id: row.github_user_id, limit: 200, offset: 0},
            });
            if (signal.aborted) {
                throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
            }
            const failure = responseFailure(response);
            if (failure) throw failure;
            const values = response.data?.assignments;
            if (!Array.isArray(values)) {
                throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
            }
            return values.filter((value): value is PermissionRow =>
                Boolean(value) && typeof value === "object"
                && typeof (value as Record<string, unknown>).node === "string",
            );
        }, {isEmpty: (items) => items.length === 0});
        memberNodes.value = memberNodesRequest.data.value ?? [];
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    }
}

function closeMemberDetails() {
    memberDetails.value = null;
    memberNodes.value = [];
    memberNodesRequest.reset();
}

function openMemberEdit(row: User) {
    if (!canManageMembers.value || mutationPending.value) return;
    memberEdit.value = row;
    memberTemplateName.value = String(row.source_id ?? "");
    void ensureTemplates();
}

function closeMemberEdit() {
    if (mutationPending.value) return;
    memberEdit.value = null;
}

async function saveMemberTemplate() {
    const row = memberEdit.value;
    const template = memberTemplateName.value.trim();
    if (!row || !template || !listNode.value || mutationPending.value) return;
    mutationPending.value = true;
    try {
        const response = await transport.request({
            action: "manage",
            node: listNode.value,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {user_id: row.github_user_id, permission_template: template},
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        memberEdit.value = null;
        toast.push(pageTFormat("memberManaged", {name: displayName(row), template}), "success");
        await load();
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
        mutationPending.value = false;
    }
}

async function removeMember() {
    const row = memberRemoveConfirm.value;
    if (!row || !listNode.value || mutationPending.value) return;
    mutationPending.value = true;
    try {
        const response = await transport.request({
            action: "remove",
            node: listNode.value,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {user_id: row.github_user_id},
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        memberRemoveConfirm.value = null;
        memberEdit.value = null;
        toast.push(pageTFormat("memberRemoved", {name: displayName(row)}), "success");
        await load();
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
        mutationPending.value = false;
    }
}

function closeInvite() {
    if (mutationPending.value) return;
    inviteDialogOpen.value = false;
}

async function sendInvite() {
    const userId = inviteUserId.value.trim();
    const template = inviteTemplateName.value.trim();
    if (!canInvite.value || !listNode.value || !userId || !template || mutationPending.value) return;
    mutationPending.value = true;
    try {
        const response = await transport.request({
            action: "invite",
            node: listNode.value,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {
                user_id: userId,
                permission_template: template,
            },
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        // 邀请令牌只在这里出现：接受入口需要邀请人把令牌交给被邀请人。
        const token = response.data?.token;
        inviteToken.value = typeof token === "string" ? token : "";
        inviteExpiresAt.value = typeof response.data?.expires_at === "number"
            ? response.data.expires_at
            : null;
        toast.push(pageTFormat("inviteSent", {name: userId}), "success");
        // 邀请改变 Team 用户派生列表，成功后刷新列表与授权快照。
        await load();
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
        mutationPending.value = false;
    }
}

async function copyInviteToken() {
    if (!inviteToken.value || !navigator.clipboard) return;
    await navigator.clipboard.writeText(inviteToken.value);
    toast.push(pageT("copied"), "success");
}

function openStatusConfirmation(user: User, action: "suspend" | "activate") {
    if (!canManageUser(user) || mutationPending.value) return;
    statusConfirmation.value = {user, action};
}

function closeStatusConfirmation() {
    if (!mutationPending.value) statusConfirmation.value = null;
}

async function confirmStatusChange() {
    const pending = statusConfirmation.value;
    if (!pending || mutationPending.value) return;
    mutationPending.value = true;
    try {
        await mutateStatus(pending.user, pending.action);
        statusConfirmation.value = null;
        toast.push(
            pageTFormat("statusChanged", {
                name: displayName(pending.user),
                action: actionLabel(pending.action),
            }),
            "success",
        );
        await load();
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
        mutationPending.value = false;
    }
}

// 状态变更需要 confirm 令牌；返回响应以便调用者决定后续刷新与提示。
async function mutateStatus(user: User, action: "suspend" | "activate") {
    const confirmation = await transport.request({
        action: "confirm",
        node: "system.users",
        data: {user_id: user.github_user_id},
    });
    const confirmationFailure = responseFailure(confirmation);
    if (confirmationFailure) throw confirmationFailure;
    const token = confirmation.data?.confirmation_token;
    if (typeof token !== "string" || !token) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    const response = await transport.request({
        action,
        node: "system.users",
        headers: {"Idempotency-Key": crypto.randomUUID()},
        data: {
            user_id: user.github_user_id,
            confirmation_token: token,
        },
    });
    const failure = responseFailure(response);
    if (failure) throw failure;
    return response;
}

function openBatch(action: "suspend" | "activate") {
    const candidates = batchTargets(action);
    if (!candidates.length || mutationPending.value) return;
    batchConfirmation.value = {action, users: candidates};
}

function closeBatchConfirmation() {
    if (!mutationPending.value) batchConfirmation.value = null;
}

// 批量是逐用户串行的 confirm + 状态变更：首个失败即停止，报告实际生效数量，
// 然后无条件刷新列表，避免界面停留在"部分已改"的陈旧状态。
async function confirmBatchChange() {
    const pending = batchConfirmation.value;
    if (!pending || mutationPending.value) return;
    const targets = pending.users;
    mutationPending.value = true;
    let done = 0;
    let failureMessage: string | null = null;
    try {
        for (const user of targets) {
            await mutateStatus(user, pending.action);
            done += 1;
        }
    } catch (cause) {
        failureMessage = cause instanceof Error ? cause.message : String(cause);
    } finally {
        mutationPending.value = false;
    }
    batchConfirmation.value = null;
    selectedUserIds.value = [];
    await load();
    if (failureMessage) {
        toast.push(
            pageTFormat("batchPartial", {
                done,
                total: targets.length,
                message: failureMessage,
            }),
            "error",
        );
        return;
    }
    toast.push(pageTFormat("batchComplete", {count: done}), "success");
}

onMounted(() => void load());
watch(() => workspace.value?.team_id, (current, previous) => {
    if (current === previous) return;
    page.value = 1;
    clearFilters();
    clearSelection();
    detailOpen.value = false;
    selectedUser.value = null;
    templateRequest.reset();
    void load();
});
watch([loginFilter, displayNameFilter, userIdFilter], scheduleFilterLoad);
watch(pageCount, () => {
    if (page.value > pageCount.value) {
        page.value = pageCount.value;
        void load();
    }
});
onBeforeUnmount(() => {
    if (filterTimer !== null) {
        window.clearTimeout(filterTimer);
        filterTimer = null;
    }
});
</script>

<template>
    <div class="users-page">
        <PageHeader
            :description="pageT('description')"
            :eyebrow="isSystemWorkspace ? baseT('shell.systemWorkspace') : baseT('shell.teamWorkspace')"
            :refresh-label="pageT('refresh')"
            :refreshing="request.loading.value"
            :title="pageT('title')"
            @refresh="load"
        >
            <template #actions>
                <UiButton
                    v-if="canInvite"
                    :disabled="mutationPending"
                    variant="primary"
                    @click="openInvite"
                >
                    {{ pageT("invite") }}
                </UiButton>
            </template>
        </PageHeader>

        <section class="summary-strip">
            <div class="summary-scope">
                <span class="eyebrow">{{ pageT("scope") }}</span>
                <strong>{{ workspaceLabel }}</strong>
            </div>
            <div class="summary-facts">
                <span class="summary-fact">
                    {{ pageTFormat(isSystemWorkspace ? "totalUsers" : "totalMembers", {count: totalUsers}) }}
                </span>
                <span v-if="hasFilters" class="summary-fact is-filtered">{{ pageT("filtered") }}</span>
                <span v-if="lastLoadedAt" class="summary-fact is-muted">
                    {{ pageT("updatedAt") }} {{ formatTimestamp(lastLoadedAt, localeName) }}
                </span>
            </div>
        </section>

        <section v-if="selectedUsers.length" class="batch-bar">
            <span>{{ pageTFormat("selectedCount", {count: selectedUsers.length}) }}</span>
            <div class="row-actions">
                <UiButton :disabled="mutationPending" variant="ghost" @click="clearSelection">
                    {{ pageT("clearSelection") }}
                </UiButton>
                <UiButton
                    :disabled="mutationPending || !batchTargets('suspend').length"
                    :title="batchTargets('suspend').length ? undefined : pageT('batchNoneHint')"
                    variant="ghost"
                    @click="openBatch('suspend')"
                >
                    {{ pageT("batchSuspend") }}
                </UiButton>
                <UiButton
                    :disabled="mutationPending || !batchTargets('activate').length"
                    :title="batchTargets('activate').length ? undefined : pageT('batchNoneHint')"
                    variant="ghost"
                    @click="openBatch('activate')"
                >
                    {{ pageT("batchActivate") }}
                </UiButton>
            </div>
        </section>

        <section :aria-label="baseT('common.userFilters')" class="filter-section">
            <UiInput
                v-model="loginFilter"
                :aria-label="pageT('searchGithubLogin')"
                :placeholder="pageT('searchGithubLogin')"
            />
            <UiInput
                v-model="displayNameFilter"
                :aria-label="pageT('searchDisplayName')"
                :placeholder="pageT('searchDisplayName')"
            />
            <UiInput
                v-model="userIdFilter"
                :aria-label="pageT('searchUserId')"
                :placeholder="pageT('searchUserId')"
            />
            <UiButton v-if="hasFilters" variant="ghost" @click="clearFilters">
                {{ pageT("clearFilters") }}
            </UiButton>
        </section>

        <p v-if="isSystemWorkspace && !managesOthers" class="read-only-notice">
            {{ pageT("readOnlyNotice") }}
        </p>

        <RequestState
            :message="!workspace ? pageT('noWorkspace') : phase === 'forbidden' ? pageT('noPermission') : emptyMessage"
            :phase="phase"
            @retry="load"
        />

        <section v-if="request.data.value && phase !== 'forbidden'" class="table-section">
            <UiDataTable :columns="columns" :rows="users">
                <template #_select="{row}">
                    <input
                        :aria-label="pageT('select')"
                        :checked="isSelected(row)"
                        :disabled="!canManageUser(row) || mutationPending"
                        type="checkbox"
                        @change="toggleSelected(row, ($event.target as HTMLInputElement).checked)"
                    />
                </template>
                <template #identity="{row}">
                    <strong>{{ displayName(row) }}</strong>
                    <span class="secondary-line">{{ row.github_user_id }}</span>
                </template>
                <template #status="{row}">
                    <span :data-status="row.status" class="status">
                        {{ statusLabel(row.status) }}
                    </span>
                </template>
                <template #source_id="{row}">
                    <span v-if="row.source_type || row.source_id">
                        {{ row.source_type || baseT("common.emptyValue") }}
                        /
                        {{ row.source_id || baseT("common.emptyValue") }}
                    </span>
                    <span v-else>{{ baseT("common.emptyValue") }}</span>
                </template>
                <template #created_at="{row}">
                    {{ formatTimestamp(row.created_at, localeName) }}
                </template>
                <template #joined_at="{row}">
                    {{ formatTimestamp(row.joined_at, localeName) }}
                </template>
                <template #last_login_at="{row}">
                    {{ formatTimestamp(row.last_login_at, localeName) }}
                </template>
                <template #actions="{row}">
                    <div v-if="!isSystemWorkspace" class="row-actions">
                        <UiButton variant="ghost" @click="openMemberDetails(row)">
                            {{ pageT("details") }}
                        </UiButton>
                        <UiButton
                            v-if="canManageMembers"
                            :disabled="mutationPending"
                            variant="ghost"
                            @click="openMemberEdit(row)"
                        >
                            {{ pageT("edit") }}
                        </UiButton>
                    </div>
                    <div v-else class="row-actions">
                        <UiButton variant="ghost" @click="openDetails(row)">
                            {{ pageT("details") }}
                        </UiButton>
                        <UiButton
                            v-if="row.status === 'active' && canSuspendUser"
                            :disabled="mutationPending || !canManageUser(row) || Boolean(statusLockReason(row))"
                            :title="!canManageUser(row) ? pageT('lockedHint') : statusLockReason(row) || undefined"
                            variant="ghost"
                            @click="openStatusConfirmation(row, 'suspend')"
                        >
                            {{ pageT("suspend") }}
                        </UiButton>
                        <UiButton
                            v-if="row.status === 'suspended' && canActivateUser"
                            :disabled="mutationPending || !canManageUser(row) || Boolean(statusLockReason(row))"
                            :title="!canManageUser(row) ? pageT('lockedHint') : statusLockReason(row) || undefined"
                            variant="ghost"
                            @click="openStatusConfirmation(row, 'activate')"
                        >
                            {{ pageT("activate") }}
                        </UiButton>
                        <UiButton
                            :disabled="mutationPending || !canManageUser(row)"
                            :title="canManageUser(row) ? undefined : pageT('lockedHint')"
                            variant="ghost"
                            @click="openNodeEditor(row)"
                        >
                            {{ pageT("nodes") }}
                        </UiButton>
                    </div>
                </template>
            </UiDataTable>
            <div class="pagination">
                <UiButton
                    :disabled="page <= 1 || request.loading.value"
                    variant="ghost"
                    @click="previousPage"
                >
                    {{ pageT("previousPage") }}
                </UiButton>
                <span>{{ pageTFormat("pageLabel", {page, count: pageCount}) }}</span>
                <UiButton
                    :disabled="page >= pageCount || request.loading.value"
                    variant="ghost"
                    @click="nextPage"
                >
                    {{ pageT("nextPage") }}
                </UiButton>
            </div>
        </section>

        <UiDialog :open="detailOpen" :title="pageT('detailsTitle')" @close="closeDetails">
            <dl v-if="detailRequest.data.value" class="details-list">
                <div><dt>{{ pageT("identity") }}</dt><dd>{{ displayName(detailRequest.data.value) }}</dd></div>
                <div><dt>{{ baseT("common.id") }}</dt><dd>{{ detailRequest.data.value.github_user_id }}</dd></div>
                <div><dt>{{ pageT("status") }}</dt><dd>{{ statusLabel(detailRequest.data.value.status) }}</dd></div>
                <div><dt>{{ pageT("created") }}</dt><dd>{{ formatTimestamp(detailRequest.data.value.created_at, localeName) }}</dd></div>
                <div><dt>{{ pageT("lastLogin") }}</dt><dd>{{ formatTimestamp(detailRequest.data.value.last_login_at, localeName) }}</dd></div>
            </dl>
            <RequestState
                :phase="detailRequest.phase.value"
                @retry="retryDetail"
            />
            <section class="permission-section">
                <h3>{{ pageT("permissions") }}</h3>
                <RequestState
                    :phase="permissionRequest.phase.value"
                    @retry="retryPermissions"
                />
                <ul v-if="permissionRequest.data.value && permissionRequest.data.value.length">
                    <li
                        v-for="(item, index) in permissionRequest.data.value"
                        :key="String(item.assignment_id ?? item.value ?? index)"
                    >
                        <code>{{ item.node || item.value }}</code>
                        <span v-if="item.effect">: {{ permissionEffectLabel(String(item.effect)) }}</span>
                        <span v-if="item.source_type || item.source_id">
                            · {{ pageT("assignmentSource") }}:
                            {{ item.source_type || baseT("common.emptyValue") }}/{{ item.source_id || baseT("common.emptyValue") }}
                        </span>
                        <span v-if="item.priority !== undefined">
                            · {{ pageT("assignmentPriority") }}: {{ item.priority }}
                        </span>
                        <span v-if="item.expires_at !== undefined">
                            · {{ pageT("assignmentExpiry") }}:
                            {{ formatTimestamp(
                                typeof item.expires_at === "number" || typeof item.expires_at === "string"
                                    ? item.expires_at
                                    : null,
                                localeName,
                            ) }}
                        </span>
                    </li>
                </ul>
                <p
                    v-else-if="permissionRequest.phase.value === 'success' && permissionRequest.data.value"
                    class="empty-copy"
                >
                    {{ pageT("permissionsEmpty") }}
                </p>
            </section>
            <template #footer>
                <UiButton variant="ghost" @click="closeDetails">{{ pageT("close") }}</UiButton>
            </template>
        </UiDialog>

        <UiDialog :open="inviteDialogOpen" :title="pageT('inviteTitle')" @close="closeInvite">
            <div v-if="inviteToken" class="invite-token">
                <p>{{ pageT("inviteTokenHint") }}</p>
                <code>{{ inviteToken }}</code>
                <p v-if="inviteExpiresAt" class="dialog-note">
                    {{ pageT("inviteTokenExpires") }}
                    {{ formatTimestamp(inviteExpiresAt, localeName) }}
                </p>
                <UiButton variant="secondary" @click="copyInviteToken">
                    {{ pageT("copy") }}
                </UiButton>
            </div>
            <div v-else class="invite-form">
                <label>
                    <span>{{ pageT("inviteUser") }}</span>
                    <UiInput
                        v-model="inviteUserId"
                        :aria-label="pageT('inviteUser')"
                        :placeholder="pageT('inviteUser')"
                    />
                </label>
                <label>
                    <span>{{ pageT("inviteTemplate") }}</span>
                    <select v-if="canReadTemplates && templates.length" v-model="inviteTemplateName">
                        <option disabled value="">{{ pageT("inviteTemplate") }}</option>
                        <option v-for="item in templates" :key="item.template_id" :value="item.name">
                            {{ item.name }}
                        </option>
                    </select>
                    <UiInput
                        v-else
                        v-model="inviteTemplateName"
                        :aria-label="pageT('inviteTemplate')"
                        :placeholder="pageT('templateFreeText')"
                    />
                </label>
                <p v-if="!canReadTemplates" class="dialog-note">{{ pageT("templateUnavailable") }}</p>
                <p v-else-if="templateLoadFailed" class="dialog-note is-error">
                    {{ baseT("state.serverError") }}
                    <button class="link-button" type="button" @click="ensureTemplates">
                        {{ baseT("state.retry") }}
                    </button>
                </p>
            </div>
            <template #footer>
                <UiButton :disabled="mutationPending" variant="ghost" @click="closeInvite">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton
                    :disabled="mutationPending || !inviteUserId.trim() || !inviteTemplateName.trim()"
                    variant="primary"
                    @click="sendInvite"
                >
                    {{ pageT("sendInvite") }}
                </UiButton>
            </template>
        </UiDialog>

        <UiDialog :open="memberDetails !== null" :title="pageT('memberDetailsTitle')" @close="closeMemberDetails">
            <div v-if="memberDetails" class="member-detail">
                <dl class="details-list">
                    <div><dt>{{ pageT("identity") }}</dt><dd>{{ displayName(memberDetails) }}</dd></div>
                    <div><dt>{{ baseT("common.id") }}</dt><dd>{{ memberDetails.github_user_id }}</dd></div>
                    <div><dt>{{ pageT("status") }}</dt><dd>{{ statusLabel(memberDetails.status) }}</dd></div>
                    <div><dt>{{ pageT("source") }}</dt><dd>{{ memberDetails.source_id || baseT("common.emptyValue") }}</dd></div>
                    <div>
                        <dt>{{ pageT("joined") }}</dt>
                        <dd>{{ formatTimestamp(memberDetails.joined_at ?? null, localeName) }}</dd>
                    </div>
                </dl>
                <h3 class="member-detail-title">{{ pageT("nodes") }}</h3>
                <p v-if="!canReadMemberNodes" class="dialog-note">{{ pageT("memberNodesDenied") }}</p>
                <template v-else>
                    <p v-if="!memberNodes.length && memberNodesRequest.phase.value === 'success'" class="dialog-note">
                        {{ pageT("memberNodesEmpty") }}
                    </p>
                    <ul v-if="memberNodes.length" class="member-nodes">
                        <li v-for="item in memberNodes" :key="String(item.assignment_id ?? item.node)">
                            <code>{{ item.node }}</code>
                            <span v-if="item.effect">: {{ permissionEffectLabel(String(item.effect)) }}</span>
                        </li>
                    </ul>
                </template>
            </div>
            <template #footer>
                <UiButton variant="ghost" @click="closeMemberDetails">{{ pageT("close") }}</UiButton>
            </template>
        </UiDialog>

        <UiDialog :open="memberEdit !== null" :title="pageT('memberEditTitle')" @close="closeMemberEdit">
            <div v-if="memberEdit" class="invite-form">
                <p class="dialog-target">{{ displayName(memberEdit) }}</p>
                <label>
                    <span>{{ pageT("memberTemplate") }}</span>
                    <select v-if="memberTemplateOptions.length" v-model="memberTemplateName">
                        <option v-for="item in memberTemplateOptions" :key="item.template_id" :value="item.name">
                            {{ item.name }}
                        </option>
                    </select>
                    <UiInput
                        v-else
                        v-model="memberTemplateName"
                        :aria-label="pageT('memberTemplate')"
                        :placeholder="pageT('templateFreeText')"
                    />
                </label>
                <p v-if="isTeamOwner(memberEdit)" class="dialog-note">{{ pageT("ownerTemplateNote") }}</p>
                <p v-if="!canReadTemplates" class="dialog-note">{{ pageT("templateUnavailable") }}</p>
            </div>
            <template #footer>
                <UiButton :disabled="mutationPending" variant="ghost" @click="closeMemberEdit">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton
                    v-if="memberEdit && !isTeamOwner(memberEdit) && canRemoveMembers"
                    :disabled="mutationPending"
                    variant="danger"
                    @click="memberRemoveConfirm = memberEdit"
                >
                    {{ pageT("memberRemove") }}
                </UiButton>
                <UiButton
                    :disabled="mutationPending || !memberTemplateName.trim()"
                    variant="primary"
                    @click="saveMemberTemplate"
                >
                    {{ pageT("save") }}
                </UiButton>
            </template>
        </UiDialog>

        <UiDialog
            :open="memberRemoveConfirm !== null"
            :title="pageT('memberRemoveTitle')"
            @close="memberRemoveConfirm = null"
        >
            <p v-if="memberRemoveConfirm" class="dialog-target">
                {{ pageTFormat("memberRemoveBody", {name: displayName(memberRemoveConfirm)}) }}
            </p>
            <template #footer>
                <UiButton :disabled="mutationPending" variant="ghost" @click="memberRemoveConfirm = null">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton :disabled="mutationPending" variant="danger" @click="removeMember">
                    {{ pageT("memberRemoveConfirm") }}
                </UiButton>
            </template>
        </UiDialog>

        <UiDialog :open="nodeDialogOpen" :title="pageT('nodesTitle')" @close="closeNodeEditor">
            <div v-if="nodeUser" class="node-form">
                <p class="dialog-target">{{ displayName(nodeUser) }}</p>
                <p class="dialog-note">{{ pageT("nodesHint") }}</p>
                <RequestState
                    :phase="userNodeRequest.phase.value"
                    @retry="loadUserNodes(nodeUser.github_user_id)"
                />
                <PermissionTreeEditor
                    v-if="userNodeRequest.phase.value === 'success'"
                    v-model="nodePermissions"
                    :effects-editable="true"
                    :label="pageT('nodes')"
                    :locked-nodes="templateNodeValues"
                    :options="nodeOptions"
                />
                <p v-else-if="nodeLoadFailed" class="dialog-note is-error">
                    {{ baseT("state.serverError") }}
                    <button class="link-button" type="button" @click="openNodeEditor(nodeUser)">
                        {{ baseT("state.retry") }}
                    </button>
                </p>
            </div>
            <template #footer>
                <UiButton :disabled="mutationPending" variant="ghost" @click="closeNodeEditor">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton
                    :disabled="mutationPending || userNodeRequest.phase.value !== 'success'"
                    variant="primary"
                    @click="saveUserNodes"
                >
                    {{ pageT("nodesSave") }}
                </UiButton>
            </template>
        </UiDialog>

        <UiDialog
            :open="statusConfirmation !== null"
            :title="pageT('confirmStatusTitle')"
            @close="closeStatusConfirmation"
        >
            <p v-if="statusConfirmation" class="dialog-target">
                {{ displayName(statusConfirmation.user) }}:
                {{ actionLabel(statusConfirmation.action) }}
            </p>
            <template #footer>
                <UiButton :disabled="mutationPending" variant="ghost" @click="closeStatusConfirmation">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton :disabled="mutationPending" variant="primary" @click="confirmStatusChange">
                    {{ pageT("confirmStatus") }}
                </UiButton>
            </template>
        </UiDialog>

        <UiDialog
            :open="batchConfirmation !== null"
            :title="pageT('batchConfirmTitle')"
            @close="closeBatchConfirmation"
        >
            <div v-if="batchConfirmation" class="batch-confirm">
                <p>
                    {{ pageTFormat("batchConfirmBody", {
                        action: actionLabel(batchConfirmation.action),
                        count: batchConfirmation.users.length,
                    }) }}
                </p>
                <ul>
                    <li v-for="user in batchConfirmation.users" :key="user.github_user_id">
                        {{ displayName(user) }}
                    </li>
                </ul>
            </div>
            <template #footer>
                <UiButton :disabled="mutationPending" variant="ghost" @click="closeBatchConfirmation">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton :disabled="mutationPending" variant="primary" @click="confirmBatchChange">
                    {{ pageT("confirmStatus") }}
                </UiButton>
            </template>
        </UiDialog>
    </div>
</template>

<style scoped>
.users-page {
    display: grid;
    gap: 24px;
}

.eyebrow {
    color: #9aa6b2;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}

.summary-strip {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 14px 16px;
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 10px;
    background: #171a21;
}

.summary-scope {
    display: grid;
    gap: 2px;
    min-width: 0;
}

.summary-facts {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 14px;
}

.summary-fact {
    font-size: 13px;
}

.summary-fact.is-filtered {
    color: #f2c14e;
}

.summary-fact.is-muted {
    color: #9aa6b2;
    font-size: 12px;
}

.batch-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 14px;
    border: 1px solid rgba(91, 140, 255, 0.35);
    border-radius: 8px;
    background: rgba(91, 140, 255, 0.08);
}

.filter-section {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr)) auto;
    gap: 10px;
    align-items: center;
}

.read-only-notice {
    margin: 0;
    color: #9aa6b2;
    font-size: 12px;
}

.table-section {
    min-width: 0;
    padding: 2px 0;
}

.secondary-line {
    display: block;
    margin-top: 4px;
    color: #9aa6b2;
    font-size: 12px;
}

.status[data-status="active"] {
    color: #78d88c;
}

.status[data-status="suspended"] {
    color: #ff9b9b;
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
    margin-top: 16px;
    color: #9aa6b2;
}

.details-list {
    display: grid;
    gap: 12px;
    margin: 0 0 16px;
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
    word-break: break-all;
}

.permission-section {
    margin-top: 18px;
}

.permission-section h3 {
    margin: 0 0 8px;
    font-size: 14px;
}

.permission-section ul {
    display: grid;
    gap: 6px;
    margin: 0;
    padding-left: 18px;
    color: #c8d5ff;
}

.empty-copy {
    margin: 8px 0 0;
    color: #9aa6b2;
}

.edit-form,
.invite-form,
.node-form {
    display: grid;
    gap: 14px;
}

.invite-token {
    display: grid;
    gap: 10px;
    justify-items: start;
}

.invite-token code {
    padding: 8px 10px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    background: #101216;
    overflow-wrap: anywhere;
}

.edit-form label,
.invite-form label {
    display: grid;
    gap: 6px;
    color: #9aa6b2;
}

.edit-form select,
.invite-form select {
    min-height: 40px;
    padding: 0 10px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    background: #101216;
    color: #edf1f7;
}

.dialog-target {
    margin: 0;
    color: #c8d5ff;
}

.dialog-note {
    margin: 0;
    color: #9aa6b2;
    font-size: 12px;
}

.dialog-note.is-error {
    color: #ff9b9b;
}

.member-detail {
    display: grid;
    gap: 14px;
}

.member-detail-title {
    margin: 0;
    font-size: 14px;
}

.member-nodes {
    display: grid;
    gap: 6px;
    margin: 0;
    padding: 0;
    list-style: none;
}

.member-nodes li {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: baseline;
    color: #d8dee9;
}

.link-button {
    margin-left: 6px;
    padding: 0;
    border: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    text-decoration: underline;
    cursor: pointer;
}

.batch-confirm {
    display: grid;
    gap: 12px;
}

.batch-confirm ul {
    display: grid;
    gap: 6px;
    margin: 0;
    padding-left: 18px;
}

@media (max-width: 640px) {
    .summary-strip {
        align-items: flex-start;
        flex-direction: column;
    }
}

@media (max-width: 520px) {
    .filter-section {
        grid-template-columns: 1fr;
    }
}
</style>
