<script lang="ts" setup>
import {computed, onBeforeUnmount, onMounted, ref, watch} from "vue";
import {RouterLink} from "vue-router";
import {transport, type TransportResponse} from "../api/transport";
import {responseFailure, RequestFailure, type RequestPhase} from "../composables/requestState";
import {useAuthorization} from "../composables/useAuthorization";
import {useRequest} from "../composables/useRequest";
import {useWorkspaceContext} from "../composables/useWorkspaceContext";
import {formatTimestamp} from "../composables/time";
import RequestState from "../components/RequestState.vue";
import UiDialog from "../components/UiDialog.vue";
import UiList from "../components/UiList.vue";
import {useLocale} from "../i18n";
import PageHeader from "../components/PageHeader.vue";

type OverviewAudit = {
    actor: string;
    action: string;
    target: string;
    metadata_json: string;
    created_at: number;
    metadata?: Record<string, unknown>;
};

type Overview = {
    workspace_kind: "system" | "team";
    team_id: string | null;
    keys: { total: number; by_status: Record<string, number> };
    grants: { total: number; expired: number };
    users: number;
    packages: number;
    uploads: { pending: number; timed_out: number };
    audit: OverviewAudit[];
    audit_total?: number;
    teams?: number;
    applications?: { pending: number };
    health?: { service: string; database: string; storage: string };
};

type AttentionSeverity = "attention" | "pending";

type AttentionItem = {
    key: string;
    label: string;
    hint: string;
    count: number;
    severity: AttentionSeverity;
    to: string | { path: string; query: Record<string, string> };
};

type HealthRow = {
    key: string;
    label: string;
    status: string;
    text: string;
    linkable: boolean;
};

type Metric = {
    key: string;
    label: string;
    value: number;
    hint: string;
    to: string;
};

const {locale, t: baseT} = useLocale();
const authorization = useAuthorization();
const request = useRequest<Overview>();
const {selected} = useWorkspaceContext();
const workspace = computed(() => selected.value);
const workspaceLabel = computed(() => workspace.value?.name ?? baseT("common.emptyValue"));
const isSystemWorkspace = computed(() => workspace.value?.workspace_kind === "system");
const teamPrefix = computed(() => workspace.value ? `team.${workspace.value.team_id}` : "");
const overviewNode = computed(() =>
    workspace.value?.workspace_kind === "team"
        ? `team.${workspace.value.team_id}.overview`
        : "system.overview",
);
const overviewPermission = computed(() => `${overviewNode.value}.read`);
const authorizationFailure = ref<RequestFailure | null>(null);
const auditFilter = ref("");
const auditPage = ref(1);
// 已下发请求的过滤词与页码，用于去重，避免同一次交互重复取数。
const requestedFilter = ref("");
const requestedPage = ref(1);
let auditQueryTimer: number | null = null;
const selectedAudit = ref<OverviewAudit | null>(null);
const lastLoadedAt = ref<Date | null>(null);
const auditPageSize = 8;

const messages = {
    zh: {
        eyebrowSystem: "系统工作区",
        eyebrowTeam: "Team 工作区",
        title: "概览",
        description: "先看待处理事项，再看工作区规模与最近活动。",
        refresh: "刷新",
        scope: "范围",
        updatedAt: "数据更新于",
        attention: "需要处理",
        attentionClear: "当前没有需要处理的事项。",
        attentionSummary: "{count} 项需要处理",
        pendingSummary: "{count} 项待完成",
        allClear: "没有待处理事项",
        attentionApplications: "Team 申请待审",
        attentionApplicationsHint: "新 Team 申请需要批准或拒绝。",
        attentionTimedOutUploads: "上传已超时",
        attentionTimedOutUploadsHint: "未完成的上传草稿已超过有效期，需要清理或重新上传。",
        attentionPendingUploads: "上传待完成",
        attentionPendingUploadsHint: "上传草稿尚未确认，确认后才会生成 Package 版本。",
        attentionExpiredGrants: "授权已过期",
        attentionExpiredGrantsHint: "有效授权已超过结束时间，需要续期或撤销。",
        resolve: "去处理",
        scale: "工作区规模",
        metricKeys: "Keys",
        metricKeysHint: "未使用 {count}",
        metricKeysHintEmpty: "均已使用或撤销",
        metricGrants: "授权",
        metricGrantsHint: "授权记录总数（含模板套用）",
        metricUsers: "用户",
        metricUsersHint: "平台内状态为可用的账户",
        metricGrantedUsers: "授权用户",
        metricGrantedUsersHint: "持有本 Team 有效授权的账户",
        metricTeams: "团队",
        metricTeamsHint: "未归档的 Team 数量",
        metricPackages: "Packages",
        metricPackagesHint: "已发布的版本数量",
        healthTitle: "服务健康",
        healthService: "服务",
        healthDatabase: "数据库",
        healthStorage: "存储",
        recentActivity: "最近活动",
        activityTotal: "共 {count} 条",
        noActivity: "暂无最近活动",
        emptyActivityFilter: "没有匹配的活动。",
        openAudit: "查看全部审计",
        auditFilter: "过滤操作者、动作或目标",
        previousPage: "上一页",
        nextPage: "下一页",
        actor: "操作者",
        action: "动作",
        target: "目标",
        created: "时间",
        metadata: "附加信息",
        close: "关闭",
        noWorkspace: "当前没有可用工作区。",
        permission: "没有查看概览的权限。",
        invalid: "概览响应格式无效。",
        status: {
            ok: "正常",
            configured: "已配置",
            unavailable: "不可用",
            error: "异常",
        },
    },
    en: {
        eyebrowSystem: "System workspace",
        eyebrowTeam: "Team workspace",
        title: "Overview",
        description: "Start with what needs attention, then review workspace scale and recent activity.",
        refresh: "Refresh",
        scope: "Scope",
        updatedAt: "Data loaded at",
        attention: "Needs attention",
        attentionClear: "Nothing needs your attention right now.",
        attentionSummary: "{count} item(s) need attention",
        pendingSummary: "{count} item(s) in progress",
        allClear: "Nothing pending",
        attentionApplications: "Team applications awaiting review",
        attentionApplicationsHint: "New Team applications must be approved or rejected.",
        attentionTimedOutUploads: "Uploads timed out",
        attentionTimedOutUploadsHint: "Draft uploads passed their expiry and need cleanup or re-upload.",
        attentionPendingUploads: "Uploads in progress",
        attentionPendingUploadsHint: "Draft uploads are not confirmed, so no package version exists yet.",
        attentionExpiredGrants: "Grants expired",
        attentionExpiredGrantsHint: "Active grants passed their end time and need renewal or revocation.",
        resolve: "Resolve",
        scale: "Workspace scale",
        metricKeys: "Keys",
        metricKeysHint: "{count} unused",
        metricKeysHintEmpty: "All used or revoked",
        metricGrants: "Grants",
        metricGrantsHint: "Authorization records, including template applications",
        metricUsers: "Users",
        metricUsersHint: "Accounts with an active platform status",
        metricGrantedUsers: "Granted users",
        metricGrantedUsersHint: "Accounts holding an active grant in this Team",
        metricTeams: "Teams",
        metricTeamsHint: "Teams that are not archived",
        metricPackages: "Packages",
        metricPackagesHint: "Published package versions",
        healthTitle: "Service health",
        healthService: "Service",
        healthDatabase: "Database",
        healthStorage: "Storage",
        recentActivity: "Recent activity",
        activityTotal: "{count} total",
        noActivity: "No recent activity",
        emptyActivityFilter: "No activity matches the filter.",
        openAudit: "View all audit",
        auditFilter: "Filter actor, action, or target",
        previousPage: "Previous page",
        nextPage: "Next page",
        actor: "Actor",
        action: "Action",
        target: "Target",
        created: "Time",
        metadata: "Metadata",
        close: "Close",
        noWorkspace: "No workspace is available.",
        permission: "You do not have permission to view the overview.",
        invalid: "The overview response is invalid.",
        status: {
            ok: "OK",
            configured: "Configured",
            unavailable: "Unavailable",
            error: "Error",
        },
    },
} as const;

const pageT = (key: string): string => {
    const value = key.split(".").reduce<unknown>(
        (current, part) => current && typeof current === "object"
            ? (current as Record<string, unknown>)[part]
            : undefined,
        messages[locale.value],
    );
    return String(value ?? key);
};

const pageTWithCount = (key: string, count: number): string =>
    pageT(key).replace("{count}", String(count));

// 后端已知的审计动作映射为可读文案；未收录的动作直接显示原始 action，
// 不做猜测，避免把事件说错。
const auditLabels: Record<string, { zh: string; en: string }> = {
    "email.enqueue": {zh: "邮件入队", en: "Email queued"},
    "email.sent": {zh: "邮件已发送", en: "Email sent"},
    "email.failed": {zh: "邮件发送失败", en: "Email failed"},
    "session.revoke": {zh: "会话已撤销", en: "Session revoked"},
    "package.publish": {zh: "Package 已发布", en: "Package published"},
    "package.confirm": {zh: "Package 上传已确认", en: "Package upload confirmed"},
    "permission_template.create": {zh: "权限模板已创建", en: "Permission template created"},
    "permission_template.update": {zh: "权限模板已更新", en: "Permission template updated"},
    "permission_template.disable": {zh: "权限模板已停用", en: "Permission template disabled"},
    "permission_assignment.create": {zh: "权限分配已创建", en: "Permission assignment created"},
    "permission_assignment.update": {zh: "权限分配已更新", en: "Permission assignment updated"},
};

const overview = computed(() => request.data.value);
const canReadOverview = computed(() => authorization.can(overviewPermission.value));
const phase = computed<RequestPhase>(() => {
    if (!workspace.value) return "empty";
    if (authorizationFailure.value) return "forbidden";
    if (authorization.phase.value !== "success") return authorization.phase.value;
    if (!canReadOverview.value) return "forbidden";
    return request.phase.value;
});
const localeName = computed(() => locale.value === "zh" ? "zh-CN" : "en-US");
const workspaceKindLabel = computed(() =>
    isSystemWorkspace.value ? pageT("eyebrowSystem") : pageT("eyebrowTeam"),
);

const canReadApplications = computed(() =>
    isSystemWorkspace.value && (
        authorization.can("system.team_applications.read")
        || authorization.can("system.team_applications.create")
    ),
);
const canHandleUploads = computed(() =>
    isSystemWorkspace.value
        ? authorization.can("system.operations.status.read")
        : authorization.can(`${teamPrefix.value}.package_uploads.create`)
            || authorization.can(`${teamPrefix.value}.packages.manage`),
);
const canReadAssignments = computed(() =>
    Boolean(teamPrefix.value)
    && authorization.can(`${teamPrefix.value}.permission_assignments.read`),
);
const canReadAudit = computed(() =>
    Boolean(teamPrefix.value) && authorization.can(`${teamPrefix.value}.audit.read`),
);
const canReadHealth = computed(() =>
    isSystemWorkspace.value
    && Boolean(overview.value?.health)
    && authorization.can("system.operations.status.read"),
);

// 待处理区只列"有权限看到、也有目标页面可处理"的事项，避免给出无效入口。
const attentionItems = computed<AttentionItem[]>(() => {
    const data = overview.value;
    if (!data || !workspace.value) return [];
    const items: AttentionItem[] = [];
    const pendingApplications = data.applications?.pending ?? 0;
    if (canReadApplications.value && pendingApplications > 0) {
        items.push({
            key: "applications",
            label: pageT("attentionApplications"),
            hint: pageT("attentionApplicationsHint"),
            count: pendingApplications,
            severity: "attention",
            to: "/overview/applications",
        });
    }
    if (canHandleUploads.value && data.uploads.timed_out > 0) {
        items.push({
            key: "timedOutUploads",
            label: pageT("attentionTimedOutUploads"),
            hint: pageT("attentionTimedOutUploadsHint"),
            count: data.uploads.timed_out,
            severity: "attention",
            // Packages 页暂无上传草稿状态视图，这里只做定位，不带无处消费的 query。
            to: "/overview/packages",
        });
    }
    if (canReadAssignments.value && data.grants.expired > 0) {
        items.push({
            key: "expiredGrants",
            label: pageT("attentionExpiredGrants"),
            hint: pageT("attentionExpiredGrantsHint"),
            count: data.grants.expired,
            severity: "attention",
            to: {
                path: "/overview/permission-assignments",
                query: {status: "expired"},
            },
        });
    }
    if (canHandleUploads.value && data.uploads.pending > 0) {
        items.push({
            key: "pendingUploads",
            label: pageT("attentionPendingUploads"),
            hint: pageT("attentionPendingUploadsHint"),
            count: data.uploads.pending,
            severity: "pending",
            to: "/overview/packages",
        });
    }
    return items.sort((left, right) =>
        Number(right.severity === "attention") - Number(left.severity === "attention"),
    );
});
const attentionCount = computed(() =>
    attentionItems.value.filter((item) => item.severity === "attention").length,
);
const pendingCount = computed(() => attentionItems.value.length - attentionCount.value);
const attentionTone = computed(() =>
    attentionCount.value > 0 ? "attention" : pendingCount.value > 0 ? "pending" : "ok",
);
const attentionSummary = computed(() => {
    if (attentionCount.value > 0) {
        return pageTWithCount("attentionSummary", attentionCount.value);
    }
    if (pendingCount.value > 0) {
        return pageTWithCount("pendingSummary", pendingCount.value);
    }
    return pageT("allClear");
});

// 规模指标：待处理类数字只在"需要处理"区出现一次，这里保留规模类计数。
const metrics = computed<Metric[]>(() => {
    const data = overview.value;
    if (!data || !workspace.value) return [];
    const prefix = teamPrefix.value;
    const unusedKeys = data.keys.by_status?.unused ?? 0;
    const keyHint = unusedKeys > 0
        ? pageTWithCount("metricKeysHint", unusedKeys)
        : data.keys.total > 0 ? pageT("metricKeysHintEmpty") : "";
    const rows: Array<Metric & {visible: boolean}> = [
        {
            key: "keys",
            label: pageT("metricKeys"),
            value: data.keys.total,
            hint: keyHint,
            to: "/overview/keys",
            visible: isSystemWorkspace.value
                ? authorization.can("team.system.keys.read")
                : authorization.can(`${prefix}.keys.read`),
        },
        {
            key: "grants",
            label: pageT("metricGrants"),
            value: data.grants.total,
            hint: pageT("metricGrantsHint"),
            to: "/overview/permission-assignments",
            visible: authorization.can(`${prefix}.permission_assignments.read`),
        },
        {
            key: "users",
            label: isSystemWorkspace.value ? pageT("metricUsers") : pageT("metricGrantedUsers"),
            value: data.users,
            hint: isSystemWorkspace.value
                ? pageT("metricUsersHint")
                : pageT("metricGrantedUsersHint"),
            to: "/overview/users",
            visible: authorization.can(
                isSystemWorkspace.value ? "system.users.read" : `${prefix}.users.read`,
            ),
        },
        {
            key: "teams",
            label: pageT("metricTeams"),
            value: data.teams ?? 0,
            hint: pageT("metricTeamsHint"),
            to: "/overview/teams",
            visible: isSystemWorkspace.value && authorization.can("system.teams.read"),
        },
        {
            key: "packages",
            label: pageT("metricPackages"),
            value: data.packages,
            hint: pageT("metricPackagesHint"),
            to: "/overview/packages",
            visible: authorization.can(
                isSystemWorkspace.value
                    ? "team.system.packages.read"
                    : `${prefix}.packages.read`,
            ),
        },
    ];
    return rows.filter((row) => row.visible);
});

const healthRows = computed<HealthRow[]>(() => {
    const health = overview.value?.health;
    if (!health || !canReadHealth.value) return [];
    const rows: Array<{key: string; labelKey: string; status: string}> = [
        {key: "service", labelKey: "healthService", status: health.service},
        {key: "database", labelKey: "healthDatabase", status: health.database},
        {key: "storage", labelKey: "healthStorage", status: health.storage},
    ];
    return rows.map((row) => ({
        key: row.key,
        label: pageT(row.labelKey),
        status: row.status,
        text: statusLabel(row.status),
        linkable: healthNeedsAttention(row.status),
    }));
});

const activity = computed(() => overview.value?.audit ?? []);
const activityTotal = computed(() => overview.value?.audit_total ?? 0);
const auditPageCount = computed(() =>
    Math.max(1, Math.ceil(activityTotal.value / auditPageSize)),
);

function auditLabel(event: OverviewAudit): string {
    const known = auditLabels[event.action];
    return known ? known[locale.value] : event.action;
}

// 失败类事件（email.failed 等）在最近活动里标异常状态，便于观察后台任务失败；
// 点击仍打开详情，不臆造跳转目标。
function isFailedAudit(event: OverviewAudit) {
    return event.action.endsWith(".failed") || event.action.endsWith(".error");
}

function auditMetadata(event: OverviewAudit) {
    if (event.metadata) return event.metadata;
    if (!event.metadata_json) return null;
    try {
        const parsed = JSON.parse(event.metadata_json);
        return parsed && typeof parsed === "object" ? parsed : null;
    } catch {
        return null;
    }
}

function statusLabel(value: string) {
    return pageT(`status.${value}`);
}

function healthNeedsAttention(value: string) {
    return value === "error" || value === "unavailable";
}

function parseOverview(response: TransportResponse): Overview {
    const failure = responseFailure(response);
    if (failure) throw failure;
    const data = response.data;
    if (
        !data
        || (data.workspace_kind !== "system" && data.workspace_kind !== "team")
        || (typeof data.team_id !== "string" && data.team_id !== null)
        || typeof data.keys !== "object"
        || typeof data.grants !== "object"
        || !Array.isArray(data.audit)
    ) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    return data as unknown as Overview;
}

async function load(refreshAuthorization = true) {
    authorizationFailure.value = null;
    if (!workspace.value) return;
    try {
        if (refreshAuthorization) {
            await authorization.ensure(transport.selectedTeamId);
        }
        if (!canReadOverview.value) {
            authorizationFailure.value = new RequestFailure(
                pageT("permission"),
                "forbidden",
                "permission_denied",
            );
            return;
        }
        await request.run(async (signal) => {
            const filter = auditFilter.value.trim();
            const page = auditPage.value;
            const response = await transport.request({
                action: "read",
                node: overviewNode.value,
                data: {
                    // 后端 audit.search 的 query 语义是 actor/action/target 任一命中，
                    // 只传 audit_actor 一个搜索词即可，不必重复填充三个字段。
                    audit_actor: filter,
                    audit_limit: auditPageSize,
                    audit_offset: (page - 1) * auditPageSize,
                },
            });
            if (signal.aborted) {
                throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
            }
            return parseOverview(response);
        });
        requestedFilter.value = auditFilter.value.trim();
        requestedPage.value = auditPage.value;
        lastLoadedAt.value = new Date();
    } catch (cause) {
        if (cause instanceof RequestFailure && cause.code === "permission_denied") {
            authorizationFailure.value = cause;
        }
    }
}

// 翻页与过滤都会换掉 audit 结果，但授权快照只需要在刷新/切换工作区时重读。
function loadActivity() {
    return load(false);
}

function previousAuditPage() {
    auditPage.value = Math.max(1, auditPage.value - 1);
    dispatchActivityQuery();
}

function nextAuditPage() {
    auditPage.value = Math.min(auditPageCount.value, auditPage.value + 1);
    dispatchActivityQuery();
}

onMounted(() => void load());
watch(
    () => [
        workspace.value?.team_id,
        workspace.value?.workspace_kind,
    ],
    ([currentTeam, currentKind], [previousTeam, previousKind]) => {
        if (currentTeam === previousTeam && currentKind === previousKind) return;
        // 切换工作区：丢掉上一个工作区的过滤与页码，整页重新取数。
        if (auditQueryTimer !== null) {
            window.clearTimeout(auditQueryTimer);
            auditQueryTimer = null;
        }
        auditFilter.value = "";
        auditPage.value = 1;
        requestedFilter.value = "";
        requestedPage.value = 1;
        void load();
    },
);

// 过滤与翻页都换成新的 audit 结果，用已下发的 (filter, page) 去重，
// 保证任何一次交互只发一个请求，且退回第 1 页也会重新取数。
function dispatchActivityQuery() {
    if (auditQueryTimer !== null) {
        window.clearTimeout(auditQueryTimer);
        auditQueryTimer = null;
    }
    const filter = auditFilter.value.trim();
    if (filter === requestedFilter.value && auditPage.value === requestedPage.value) return;
    requestedFilter.value = filter;
    requestedPage.value = auditPage.value;
    void loadActivity();
}

watch(auditFilter, () => {
    if (auditQueryTimer !== null) window.clearTimeout(auditQueryTimer);
    auditQueryTimer = window.setTimeout(() => {
        auditQueryTimer = null;
        auditPage.value = 1;
        dispatchActivityQuery();
    }, 300);
});
// 过滤后总数变小导致当前页超出范围时，收回到最后一页并重新取数。
watch(auditPageCount, () => {
    if (auditPage.value > auditPageCount.value) {
        auditPage.value = auditPageCount.value;
        dispatchActivityQuery();
    }
});
onBeforeUnmount(() => {
    if (auditQueryTimer !== null) window.clearTimeout(auditQueryTimer);
});
</script>

<template>
    <div class="overview-page">
        <PageHeader
            :description="pageT('description')"
            :eyebrow="workspaceKindLabel"
            :refresh-label="pageT('refresh')"
            :refreshing="request.loading.value"
            :title="pageT('title')"
            @refresh="load()"
        />

        <RequestState
            :message="!workspace ? pageT('noWorkspace') : phase === 'forbidden' ? pageT('permission') : null"
            :phase="phase"
            @retry="load()"
        />

        <template v-if="overview && phase !== 'forbidden'">
            <section class="status-strip">
                <div class="status-scope">
                    <span class="eyebrow">{{ pageT("scope") }}</span>
                    <strong>{{ workspaceLabel }}</strong>
                </div>
                <div class="status-facts">
                    <span
                        :data-tone="attentionTone"
                        class="status-fact"
                    >
                        <span aria-hidden="true" class="status-dot"></span>
                        {{ attentionSummary }}
                    </span>
                    <span v-if="lastLoadedAt" class="status-fact is-muted">
                        {{ pageT("updatedAt") }}
                        {{ formatTimestamp(lastLoadedAt, localeName) }}
                    </span>
                </div>
            </section>

            <section :class="{'has-health': healthRows.length > 0}" class="split-grid">
                <article class="section-block">
                    <div class="section-heading">
                        <h3>{{ pageT("attention") }}</h3>
                    </div>
                    <ul v-if="attentionItems.length" class="attention-list">
                        <li v-for="item in attentionItems" :key="item.key">
                            <RouterLink
                                :data-severity="item.severity"
                                :to="item.to"
                                class="attention-row"
                            >
                                <span aria-hidden="true" class="attention-mark"></span>
                                <span class="attention-body">
                                    <strong>{{ item.label }}</strong>
                                    <span class="attention-hint">{{ item.hint }}</span>
                                </span>
                                <span class="attention-count">{{ item.count }}</span>
                                <span class="attention-action">{{ pageT("resolve") }} →</span>
                            </RouterLink>
                        </li>
                    </ul>
                    <p v-else class="empty-copy">{{ pageT("attentionClear") }}</p>
                </article>

                <article v-if="healthRows.length" class="section-block">
                    <div class="section-heading">
                        <h3>{{ pageT("healthTitle") }}</h3>
                    </div>
                    <dl class="health-list">
                        <div v-for="row in healthRows" :key="row.key">
                            <dt>{{ row.label }}</dt>
                            <dd :data-status="row.status">
                                <span aria-hidden="true" class="health-dot"></span>
                                <RouterLink
                                    v-if="row.linkable"
                                    :aria-label="row.label"
                                    to="/overview/operations"
                                >
                                    {{ row.text }}
                                </RouterLink>
                                <span v-else>{{ row.text }}</span>
                            </dd>
                        </div>
                    </dl>
                </article>
            </section>

            <section class="section-block">
                <div class="section-heading">
                    <h3>{{ pageT("scale") }}</h3>
                </div>
                <div class="metric-grid">
                    <RouterLink
                        v-for="metric in metrics"
                        :key="metric.key"
                        :to="metric.to"
                        class="metric"
                    >
                        <span class="metric-label">{{ metric.label }}</span>
                        <strong>{{ metric.value }}</strong>
                        <span v-if="metric.hint" class="metric-hint">{{ metric.hint }}</span>
                    </RouterLink>
                </div>
            </section>

            <section class="section-block">
                <div class="section-heading">
                    <h3>{{ pageT("recentActivity") }}</h3>
                    <span class="section-heading-actions">
                        <span class="section-count">
                            {{ pageTWithCount("activityTotal", activityTotal) }}
                        </span>
                        <RouterLink
                            v-if="canReadAudit"
                            class="link-action"
                            to="/overview/audit"
                        >
                            {{ pageT("openAudit") }}
                        </RouterLink>
                    </span>
                </div>
                <div class="audit-controls">
                    <input
                        v-model="auditFilter"
                        :aria-label="pageT('auditFilter')"
                        :placeholder="pageT('auditFilter')"
                        type="search"
                    />
                </div>
                <UiList v-if="activity.length" :items="activity">
                    <template #default="{item}">
                        <button
                            :class="{'audit-row': true, 'is-failed': isFailedAudit(item)}"
                            type="button"
                            @click="selectedAudit = item"
                        >
                            <span class="audit-main">
                                <strong>{{ auditLabel(item) }}</strong>
                                <span v-if="auditLabels[item.action]" class="audit-raw">
                                    {{ item.action }}
                                </span>
                            </span>
                            <span class="audit-actor">{{ item.actor }}</span>
                            <span class="audit-target">{{ item.target }}</span>
                            <time>{{ formatTimestamp(item.created_at, localeName) }}</time>
                        </button>
                    </template>
                </UiList>
                <p v-else class="empty-copy">
                    {{ auditFilter.trim() ? pageT("emptyActivityFilter") : pageT("noActivity") }}
                </p>
                <div v-if="auditPageCount > 1" class="audit-pagination">
                    <button
                        :aria-label="pageT('previousPage')"
                        :disabled="auditPage === 1"
                        type="button"
                        @click="previousAuditPage"
                    >
                        ‹
                    </button>
                    <span>{{ auditPage }} / {{ auditPageCount }}</span>
                    <button
                        :aria-label="pageT('nextPage')"
                        :disabled="auditPage === auditPageCount"
                        type="button"
                        @click="nextAuditPage"
                    >
                        ›
                    </button>
                </div>
            </section>
        </template>

        <UiDialog
            v-if="selectedAudit"
            :open="true"
            :title="auditLabel(selectedAudit)"
            @close="selectedAudit = null"
        >
            <dl class="audit-details">
                <div><dt>{{ pageT("action") }}</dt><dd><code>{{ selectedAudit.action }}</code></dd></div>
                <div><dt>{{ pageT("actor") }}</dt><dd>{{ selectedAudit.actor }}</dd></div>
                <div><dt>{{ pageT("target") }}</dt><dd>{{ selectedAudit.target }}</dd></div>
                <div><dt>{{ pageT("created") }}</dt><dd>{{ formatTimestamp(selectedAudit.created_at, localeName) }}</dd></div>
            </dl>
            <template v-if="auditMetadata(selectedAudit)">
                <h3 class="details-heading">{{ pageT("metadata") }}</h3>
                <pre class="details-metadata">{{ JSON.stringify(auditMetadata(selectedAudit), null, 4) }}</pre>
            </template>
            <template #footer>
                <button type="button" @click="selectedAudit = null">{{ pageT("close") }}</button>
            </template>
        </UiDialog>
    </div>
</template>

<style scoped>
.overview-page {
    display: grid;
    gap: 24px;
}

.section-block {
    min-width: 0;
    padding-top: 2px;
}

.section-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
}

.section-heading h3 {
    margin: 0;
}

.section-heading-actions {
    display: flex;
    align-items: center;
    gap: 12px;
}

.section-count {
    color: #9aa6b2;
    font-size: 12px;
}

.empty-copy {
    margin: 8px 0 0;
    color: #9aa6b2;
}

.link-action {
    color: #8eacff;
    font-size: 12px;
    text-decoration: none;
}

.link-action:hover {
    text-decoration: underline;
}

.status-strip {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 14px 16px;
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 10px;
    background: #171a21;
}

.status-scope {
    display: grid;
    gap: 2px;
    min-width: 0;
}

.status-facts {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 14px;
}

.status-fact {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
}

.status-fact[data-tone="attention"] {
    color: #f2c14e;
}

.status-fact[data-tone="pending"] {
    color: #8eacff;
}

.status-fact[data-tone="ok"] {
    color: #78d88c;
}

.status-fact.is-muted {
    color: #9aa6b2;
    font-size: 12px;
}

.status-dot {
    width: 7px;
    height: 7px;
    flex: 0 0 7px;
    border-radius: 50%;
    background: currentColor;
}

.split-grid {
    display: grid;
    gap: 28px;
    grid-template-columns: minmax(0, 1fr);
}

.split-grid.has-health {
    grid-template-columns: minmax(0, 1.3fr) minmax(240px, 0.7fr);
}

.attention-list {
    display: grid;
    gap: 8px;
    margin: 12px 0 0;
    padding: 0;
    list-style: none;
}

.attention-list > li {
    min-width: 0;
}

.attention-row {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 14px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 10px;
    background: rgba(255, 255, 255, 0.025);
    color: inherit;
    text-decoration: none;
}

.attention-row:hover {
    border-color: rgba(91, 140, 255, 0.55);
}

.attention-row[data-severity="attention"] {
    border-left: 3px solid #f2c14e;
}

.attention-row[data-severity="pending"] {
    border-left: 3px solid #8eacff;
}

.attention-mark {
    width: 8px;
    height: 8px;
    flex: 0 0 8px;
    border-radius: 50%;
    background: #f2c14e;
}

.attention-row[data-severity="pending"] .attention-mark {
    background: #8eacff;
}

.attention-body {
    display: grid;
    gap: 2px;
    min-width: 0;
    margin-right: auto;
}

.attention-body strong {
    font-size: 14px;
}

.attention-hint {
    color: #9aa6b2;
    font-size: 12px;
}

.attention-count {
    flex: 0 0 auto;
    font-size: 20px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
}

.attention-action {
    flex: 0 0 auto;
    color: #8eacff;
    font-size: 12px;
    white-space: nowrap;
}

.health-list {
    display: grid;
    gap: 12px;
    margin: 12px 0 0;
}

.health-list div {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding-bottom: 10px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.health-list dd {
    display: inline-flex;
    align-items: center;
    gap: 6px;
}

.health-dot {
    width: 7px;
    height: 7px;
    flex: 0 0 7px;
    border-radius: 50%;
    background: currentColor;
}

dt,
dd {
    margin: 0;
}

dt {
    color: #9aa6b2;
}

dd[data-status="ok"],
dd[data-status="configured"] {
    color: #78d88c;
}

dd[data-status="error"],
dd[data-status="unavailable"] {
    color: #ff9b9b;
}

dd a {
    color: inherit;
}

.metric-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
    margin-top: 12px;
}

.metric {
    display: grid;
    gap: 6px;
    align-content: start;
    min-height: 116px;
    padding: 16px;
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 8px;
    background: #171a21;
    color: inherit;
    text-decoration: none;
    cursor: pointer;
}

.metric:hover {
    border-color: rgba(91, 140, 255, 0.55);
}

.metric-label {
    color: #9aa6b2;
    font-size: 13px;
}

.metric strong {
    font-size: 26px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
}

.metric-hint {
    color: #9aa6b2;
    font-size: 12px;
}

.metric:focus-visible,
.attention-row:focus-visible,
.audit-row:focus-visible,
.audit-controls input:focus-visible,
.audit-pagination button:focus-visible {
    outline: 2px solid #8eacff;
    outline-offset: 2px;
}

.audit-controls {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 12px 0 4px;
}

.audit-controls input {
    width: min(100%, 360px);
    min-height: 38px;
    padding: 8px 11px;
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 8px;
    background: #171a21;
    color: #edf1f7;
    font: inherit;
}

.audit-row {
    display: flex;
    align-items: center;
    gap: 14px;
    width: 100%;
    min-width: 0;
    padding: 0;
    border: 0;
    background: transparent;
    color: inherit;
    text-align: left;
    cursor: pointer;
}

.audit-main {
    display: flex;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
    margin-right: auto;
}

.audit-main strong {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.audit-raw {
    flex: 0 0 auto;
    color: #7d8894;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
}

.audit-actor,
.audit-target,
.audit-row time {
    flex: 0 0 auto;
    color: #9aa6b2;
    font-size: 12px;
}

.audit-target {
    max-width: 220px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.audit-row.is-failed strong {
    color: #ff9b9b;
}

.audit-row.is-failed::after {
    content: "!";
    flex: 0 0 auto;
    display: grid;
    place-items: center;
    width: 18px;
    height: 18px;
    border: 1px solid rgba(255, 155, 155, 0.55);
    border-radius: 50%;
    color: #ff9b9b;
    font-size: 12px;
    font-weight: 700;
}

.audit-pagination {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 10px;
    margin-top: 8px;
    color: #9aa6b2;
}

.audit-pagination button {
    min-width: 34px;
    min-height: 34px;
    padding: 0 8px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 6px;
    background: #171a21;
    color: inherit;
    cursor: pointer;
}

.audit-pagination button:disabled {
    opacity: 0.45;
    cursor: default;
}

.audit-details {
    display: grid;
    gap: 10px;
    margin: 0;
}

.audit-details div {
    display: flex;
    justify-content: space-between;
    gap: 16px;
}

.audit-details dd {
    min-width: 0;
    word-break: break-all;
    text-align: right;
}

.audit-details code {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
}

.details-heading {
    margin: 18px 0 6px;
    color: #9aa6b2;
    font-size: 12px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}

.details-metadata {
    margin: 0;
    padding: 12px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    background: #101216;
    color: #c8d5ff;
    font-size: 12px;
    overflow-x: auto;
}

@media (max-width: 980px) {
    .split-grid.has-health {
        grid-template-columns: minmax(0, 1fr);
    }
}

@media (max-width: 860px) {
    .metric-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
}

@media (max-width: 640px) {
    .status-strip {
        align-items: flex-start;
        flex-direction: column;
    }

    .attention-hint,
    .audit-target {
        display: none;
    }
}

@media (max-width: 520px) {
    .audit-controls {
        align-items: stretch;
        flex-direction: column;
    }

    .audit-controls input {
        width: 100%;
    }

    .audit-row {
        align-items: flex-start;
        flex-direction: column;
        gap: 4px;
    }

    .metric-grid {
        grid-template-columns: 1fr;
    }
}
</style>
