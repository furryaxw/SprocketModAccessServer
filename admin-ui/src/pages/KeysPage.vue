<script lang="ts" setup>
import {computed, onMounted, ref, watch} from "vue";
import {transport, type TransportResponse} from "../api/transport";
import {RequestFailure, responseFailure, type RequestPhase} from "../composables/requestState";
import {useAuthorization} from "../composables/useAuthorization";
import {useRequest} from "../composables/useRequest";
import {useWorkspaceContext} from "../composables/useWorkspaceContext";
import UiDataTable from "../components/UiDataTable.vue";
import RequestState from "../components/RequestState.vue";
import {formatTimestamp} from "../composables/time";
import {useLocale} from "../i18n";
import UiButton from "../components/UiButton.vue";
import UiDialog from "../components/UiDialog.vue";
import ValidityInput from "../components/ValidityInput.vue";
import UiInput from "../components/UiInput.vue";
import {useToast} from "../composables/useToast";
import PageHeader from "../components/PageHeader.vue";
import PermissionTreeEditor from "../components/PermissionTreeEditor.vue";

type Key = {
    key_id: string;
    batch_id?: string;
    status?: string;
    permissions?: string[];
    assignments?: Array<Record<string, unknown>>;
    note?: string;
    expires_at?: number | null;
    created_at?: number;
    redeemed_at?: number | null;
    team_id?: string | null;
    // 明文随库存保存，有 keys.read 即可回读（发行前写入的旧 Key 为空）。
    plaintext?: string;
};

type KeyResponse = {
    keys: Key[];
    counts: Record<string, number>;
    total: number;
    limit: number;
    offset: number;
};

const messages = {
    zh: {
        title: "Keys",
        description: "查看当前工作区的密钥状态和授权范围。",

        refresh: "刷新",
        key: "Key",
        team: "Team",
        status: "状态",

        permissions: "权限数量",
        expires: "过期时间",
        created: "创建时间",
        note: "备注",
        unused: "未使用",

        redeemed: "已兑换",

        expired: "已过期",

        revoked: "已失效",

        permission: "没有查看 Keys 的权限。",

        noWorkspace: "Keys 需要先选择工作区。",

        invalid: "Keys 响应格式无效。",

        never: "永不过期",
        issue: "发行 Keys",
        issueTitle: "批量发行 Keys",
        quantity: "数量",
        permissionList: "权限节点",

        issueConfirm: "发行",
        issueReview: "发行预览",
        issueReviewQuantity: "数量",
        issueReviewPermissions: "授权节点",
        issueReviewExpires: "有效期",
        issueExpires: "有效期至",
        close: "关闭",
        issued: "已发行；明文随库存保存，之后仍可在列表里查看。",

        plaintext: "明文",
        copy: "复制",
        copied: "已复制",

        download: "下载",
        downloaded: "已下载",

        manage: "管理",
        manageTitle: "管理 Key",
        batchTitle: "Key 批次详情",
        batch: "批次",
        batchQuantity: "数量",
        batchCreated: "创建时间",
        batchOwner: "创建者",

        batchCounts: "状态统计",

        manageConfirm: "保存",
        notePlaceholder: "备注",
        saveDone: "Key 已更新。",

        prefix: "过滤 Key 前缀",
        filterStatus: "状态过滤",

        allStatuses: "全部状态",

        previousPage: "上一页",

        nextPage: "下一页",

    },
    en: {
        title: "Keys",
        description: "Review key status and authorization scope for the current workspace.",
        refresh: "Refresh",
        key: "Key",
        team: "Team",
        status: "Status",
        permissions: "Permissions",
        expires: "Expires",
        created: "Created",
        note: "Note",
        unused: "Unused",
        redeemed: "Redeemed",
        expired: "Expired",
        revoked: "Revoked",
        permission: "You do not have permission to view Keys.",
        noWorkspace: "Select a workspace to view Keys.",
        invalid: "The Keys response is invalid.",
        never: "Never",
        issue: "Issue Keys",
        issueTitle: "Issue Keys",
        quantity: "Quantity",
        permissionList: "Permission nodes",
        issueConfirm: "Issue",
        issueReview: "Issue preview",
        issueReviewQuantity: "Quantity",
        issueReviewPermissions: "Permission nodes",
        issueReviewExpires: "Expires",
        issueExpires: "Valid until",
        close: "Close",
        issued: "Issued; the plaintext is stored and stays readable in the list.",
        plaintext: "Plaintext",
        copy: "Copy",
        copied: "Copied",
        download: "Download",
        downloaded: "Downloaded",
        manage: "Manage",
        manageTitle: "Manage key",
        keyPermissions: "Key permissions",
        keyAssignments: "Key assignments",
        assignmentEffect: "Effect",
        assignmentPriority: "Priority",
        assignmentSource: "Source",
        assignmentExpiry: "Expiry",
        batchTitle: "Key batch details",
        batch: "Batch",
        batchQuantity: "Quantity",
        batchCreated: "Created",
        batchOwner: "Created by",
        batchCounts: "Status counts",
        manageConfirm: "Save",
        notePlaceholder: "Note",
        saveDone: "Key updated.",
        prefix: "Filter by key prefix",
        filterStatus: "Filter by status",
        allStatuses: "All statuses",
        previousPage: "Previous page",
        nextPage: "Next page",
        batchPermissions: "Batch permissions",
    },
} as const;

const zhExtra = {
    keyPermissions: "Key 权限",
    keyAssignments: "Key 授权来源",
    assignmentEffect: "效果",
    assignmentPriority: "优先级",

    assignmentSource: "来源",
    assignmentExpiry: "有效期",

    batchPermissions: "批次权限",
} as const;

const {locale, t: baseT} = useLocale();
const authorization = useAuthorization();
const {selected} = useWorkspaceContext();
const request = useRequest<KeyResponse>();
const catalog = ref<string[]>([]);
const toast = useToast();
const permissionFailure = ref<RequestFailure | null>(null);
const issueDialogOpen = ref(false);
const issueQuantity = ref("1");
const issueExpiresAt = ref<number | null>(null);
const issuePermissions = ref<Record<string, string>>({});
const issueReviewOpen = ref(false);
const issuePreview = ref<{quantity: number; permissions: string[]; expiresAt: number | null}>({
    quantity: 0,
    permissions: [],
    expiresAt: null,
});
const issuedKeys = ref<string[]>([]);
const mutationPending = ref(false);
const workspace = computed(() => selected.value);
const node = computed(() => workspace.value
    ? `team.${workspace.value.team_id}.keys`
    : "");
const canRead = computed(() => node.value ? authorization.can(`${node.value}.read`) : false);
const canDistribute = computed(() => node.value ? authorization.can(`${node.value}.distribute`) : false);
// Key 发行按节点本身做 require_grantable（后端 keys/resources.py），所以用快照的
// grantable 判定：不可分发的节点在树里直接给出禁止勾选与说明。
function canGrantNode(permission: string) {
    return authorization.canGrant(permission);
}
const canManage = computed(() => node.value ? authorization.can(`${node.value}.manage`) : false);
// System 工作区列出所有 Team 的 Key（后端平台口径），因此多一列显示归属。
const isSystemWorkspace = computed(() => workspace.value?.workspace_kind === "system");
const columns = computed<Array<{key: keyof Key; label: string}>>(() => {
    const values: Array<{key: keyof Key; label: string}> = [
        {key: "key_id", label: pageT("key")},
        {key: "plaintext", label: pageT("plaintext")},
    ];
    if (isSystemWorkspace.value) values.push({key: "team_id", label: pageT("team")});
    values.push(
        {key: "status", label: pageT("status")},
        {key: "permissions", label: pageT("permissions")},
        {key: "expires_at", label: pageT("expires")},
        {key: "created_at", label: pageT("created")},
        {key: "note", label: pageT("note")},
        {key: "batch_id", label: pageT("manage")},
    );
    return values;
});
// Team 工作区只展示本 Team 的节点：目录是全平台的，不裁剪就会把别的 Team 与系统节点
// 摆在面前，看起来"可以分发 Team 之外的权限"。
const editorCatalog = computed(() => {
    const current = workspace.value;
    return current && current.workspace_kind !== "system"
        ? catalog.value.filter((value) => value.startsWith(`team.${current.team_id}.`))
        : catalog.value;
});
const manageDialogOpen = ref(false);
const selectedKey = ref<Key | null>(null);
const manageNote = ref("");
const manageStatus = ref("unused");
const manageExpiresAt = ref<number | null>(null);
const batchDialogOpen = ref(false);
const selectedBatch = ref<Key | null>(null);
const batchRequest = useRequest<Record<string, unknown>>();
const prefixFilter = ref("");
const statusFilter = ref("");
const page = ref(1);
const pageSize = 50;
const phase = computed<RequestPhase>(() => {
    if (!workspace.value) return "empty";
    if (permissionFailure.value) return "forbidden";
    if (authorization.phase.value !== "success") return authorization.phase.value;
    if (!canRead.value) return "forbidden";
    return request.phase.value;
});
const localeName = computed(() => locale.value === "zh" ? "zh-CN" : "en-US");

function pageT(key: string) {
    const value = key.split(".").reduce<unknown>(
        (current, part) => current && typeof current === "object"
            ? (current as Record<string, unknown>)[part]
            : undefined,
        messages[locale.value],
    );
    if (value !== undefined) return String(value);
    if (locale.value === "zh") {
        const zhValue = key.split(".").reduce<unknown>(
            (current, part) => current && typeof current === "object"
                ? (current as Record<string, unknown>)[part]
                : undefined,
            zhExtra,
        );
        if (zhValue !== undefined) return String(zhValue);
    }
    return String(key);
}

function parseKeys(response: TransportResponse): KeyResponse {
    const failure = responseFailure(response);
    if (failure) throw failure;
    const values = response.data?.keys;
    const counts = response.data?.counts;
    if (!Array.isArray(values) || !counts || typeof counts !== "object") {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    if (Array.isArray(response.data?.catalog)) {
        catalog.value = response.data.catalog.filter(
            (value): value is string => typeof value === "string",
        );
    }
    return {
        keys: values.filter((value): value is Key =>
            Boolean(value)
            && typeof value === "object"
            && typeof (value as Record<string, unknown>).key_id === "string",
        ),
        counts: counts as Record<string, number>,
        total: Number(response.data?.total ?? values.length),
        limit: Number(response.data?.limit ?? pageSize),
        offset: Number(response.data?.offset ?? 0),
    };
}

async function load() {
    permissionFailure.value = null;
    if (!workspace.value) return;
    try {
        await authorization.ensure(transport.selectedTeamId);
        if (!canRead.value) {
            permissionFailure.value = new RequestFailure(pageT("permission"), "forbidden", "permission_denied");
            return;
        }
        await request.run(async (signal) => {
            const response = await transport.request({
                action: "read",
                node: node.value,
                data: {
                    status: statusFilter.value || undefined,
                    prefix: prefixFilter.value.trim(),
                    limit: pageSize,
                    offset: (page.value - 1) * pageSize,
                },
            });
            if (signal.aborted) {
                throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
            }
            return parseKeys(response);
        }, {isEmpty: (value) => value.keys.length === 0});
    } catch (cause) {
        if (cause instanceof RequestFailure && cause.code === "permission_denied") {
            permissionFailure.value = cause;
        }
    }
}

function statusLabel(status?: string) {
    if (status === "unused") return pageT("unused");
    if (status === "redeemed") return pageT("redeemed");
    if (status === "expired") return pageT("expired");
    if (status === "revoked") return pageT("revoked");
    return pageT("unused");
}

function effectiveStatus(key: Key) {
    const now = Math.floor(Date.now() / 1000);
    if (key.status === "unused" && key.expires_at !== null && key.expires_at !== undefined && key.expires_at <= now) {
        return "expired";
    }
    return key.status ?? "unused";
}

function assignmentSource(assignment: Record<string, unknown>) {
    const sourceType = typeof assignment.source_type === "string" ? assignment.source_type : "";
    const sourceId = typeof assignment.source_id === "string" ? assignment.source_id : "";
    return sourceType || sourceId ? `${sourceType}/${sourceId}` : baseT("common.emptyValue");
}

function assignmentExpiry(assignment: Record<string, unknown>) {
    const expiresAt = assignment.expires_at;
    if (typeof expiresAt !== "number" && typeof expiresAt !== "string") {
        return pageT("never");
    }
    return formatTimestamp(expiresAt, localeName.value);
}

function openManage(key: Key) {
    if (!canManage.value || mutationPending.value) return;
    selectedKey.value = key;
    manageNote.value = key.note ?? "";
    // 状态回传用库内原始状态（unused/redeemed/revoked），不用派生的
    // expired；仅未使用且未过期的键显示「未使用」选项。
    manageStatus.value = key.status ?? "unused";
    manageExpiresAt.value = key.expires_at ?? null;
    manageDialogOpen.value = true;
}

// Manage 弹窗状态下拉的可选项：仅 unused 键保留「未使用」，其余
// 只能转到 revoked（保持当前状态或撤销）。
function manageableStatusOptions(): string[] {
    const key = selectedKey.value;
    if (!key) return ["revoked"];
    return effectiveStatus(key) === "unused" ? ["unused", "revoked"] : ["revoked"];
}

async function openBatch(key: Key) {
    if (!key.batch_id || !canRead.value || mutationPending.value) return;
    selectedBatch.value = key;
    batchDialogOpen.value = true;
    batchRequest.reset();
    await batchRequest.run(async (signal) => {
        const response = await transport.request({
            action: "read_batch",
            node: node.value,
            data: {batch_id: key.batch_id},
        });
        if (signal.aborted) {
            throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
        }
        const failure = responseFailure(response);
        if (failure) throw failure;
        if (!response.data || typeof response.data.batch_id !== "string") {
            throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
        }
        return response.data;
    }).catch((cause) => {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    });
}

function closeBatch() {
    if (batchRequest.loading.value) return;
    batchDialogOpen.value = false;
    selectedBatch.value = null;
    batchRequest.reset();
}

function closeManage() {
    if (mutationPending.value) return;
    manageDialogOpen.value = false;
    selectedKey.value = null;
    manageExpiresAt.value = null;
}

async function saveManage() {
    const key = selectedKey.value;
    if (!key || !canManage.value || mutationPending.value) return;
    mutationPending.value = true;
    try {
        const update = {
            key_id: key.key_id,
            note: manageNote.value.trim(),
            status: manageStatus.value,
            expires_at: manageExpiresAt.value,
        };
        const preview = await transport.request({
            action: "manage",
            node: node.value,
            data: {...update, preview: true},
        });
        const previewFailure = responseFailure(preview);
        if (previewFailure) throw previewFailure;
        const previewData = preview.data;
        if (!previewData || typeof previewData !== "object") {
            throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
        }
        let confirmationToken: string | undefined;
        if ((previewData as Record<string, unknown>).requires_confirmation === true) {
            const confirmation = await transport.request({
                action: "confirm",
                node: node.value,
                data: {key_id: key.key_id},
            });
            const confirmationFailure = responseFailure(confirmation);
            if (confirmationFailure) throw confirmationFailure;
            const token = confirmation.data?.confirmation_token;
            if (typeof token !== "string" || !token) {
                throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
            }
            confirmationToken = token;
        }
        const response = await transport.request({
            action: "manage",
            node: node.value,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {...update, ...(confirmationToken ? {confirmation_token: confirmationToken} : {})},
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        manageDialogOpen.value = false;
        selectedKey.value = null;
        toast.push(pageT("saveDone"), "success");
        await load();
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
        mutationPending.value = false;
    }
}

function openIssue() {
    if (!canDistribute.value || mutationPending.value) return;
    issueDialogOpen.value = true;
    issuedKeys.value = [];
    issueExpiresAt.value = null;
}

function closeIssue() {
    if (mutationPending.value) return;
    issueDialogOpen.value = false;
    issueReviewOpen.value = false;
    issuedKeys.value = [];
}

async function issue() {
    if (!canDistribute.value || mutationPending.value) return;
    const quantity = Number.parseInt(issueQuantity.value, 10);
    const permissions = Object.keys(issuePermissions.value)
        .map((node) => node.trim())
        .filter(Boolean);
    if (!Number.isInteger(quantity) || quantity < 1 || permissions.length === 0) {
        toast.push(pageT("permissionList"), "error");
        return;
    }
    issuePreview.value = {quantity, permissions, expiresAt: issueExpiresAt.value};
    issueReviewOpen.value = true;
}

function closeIssueReview() {
    if (!mutationPending.value) issueReviewOpen.value = false;
}

async function confirmIssue() {
    if (!canDistribute.value || mutationPending.value || !issuePreview.value.permissions.length) {
        return;
    }
    mutationPending.value = true;
    try {
        const response = await transport.request({
            action: "distribute",
            node: node.value,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {
                quantity: issuePreview.value.quantity,
                permissions: issuePreview.value.permissions,
                expires_at: issuePreview.value.expiresAt,
            },
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        const values = response.data?.keys;
        if (!Array.isArray(values)) {
            throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
        }
        issuedKeys.value = values
            .filter((value): value is Record<string, unknown> =>
                Boolean(value) && typeof value === "object" && typeof (value as Record<string, unknown>).plaintext === "string",
            )
            .map((value) => String(value.plaintext));
        issueReviewOpen.value = false;
        toast.push(pageT("issued"), "success");
        await load();
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
        mutationPending.value = false;
    }
}

async function copyKeys() {
    if (!issuedKeys.value.length || !navigator.clipboard) return;
    await navigator.clipboard.writeText(issuedKeys.value.join("\n"));
    toast.push(pageT("copied"), "success");
}

async function copyText(value?: string) {
    if (!value || !navigator.clipboard) return;
    await navigator.clipboard.writeText(value);
    toast.push(pageT("copied"), "success");
}

function downloadKeys() {
    if (!issuedKeys.value.length) return;
    const blob = new Blob([`${issuedKeys.value.join("\n")}\n`], {type: "text/plain"});
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "sprocket-access-keys.txt";
    anchor.click();
    URL.revokeObjectURL(url);
    toast.push(pageT("downloaded"), "success");
}

onMounted(load);
watch([prefixFilter, statusFilter], reloadFromFilter);
watch(() => workspace.value?.team_id, (current, previous) => {
    if (current === previous) return;
    page.value = 1;
    void load();
});

const pageCount = computed(() => Math.max(
    1,
    Math.ceil((request.data.value?.total ?? 0) / pageSize),
));

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
</script>

<template>
    <div class="keys-page">
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
                    v-if="canDistribute"
                    variant="primary"
                    :disabled="mutationPending"
                    @click="openIssue"
                >
                    {{ pageT("issue") }}
                </UiButton>
            </template>
        </PageHeader>

        <RequestState
            :phase="phase"
            :message="!workspace ? pageT('noWorkspace') : phase === 'forbidden' ? pageT('permission') : null"
            @retry="load"
            @cancel="request.cancel"
        />

        <template v-if="request.data.value && phase !== 'forbidden'">
            <section class="filter-section">
                <UiInput
                    v-model="prefixFilter"
                    :aria-label="pageT('prefix')"
                    :placeholder="pageT('prefix')"
                />
                <select v-model="statusFilter" :aria-label="pageT('filterStatus')">
                    <option value="">{{ pageT("allStatuses") }}</option>
                    <option value="unused">{{ pageT("unused") }}</option>
                    <option value="redeemed">{{ pageT("redeemed") }}</option>
                    <option value="expired">{{ pageT("expired") }}</option>
                    <option value="revoked">{{ pageT("revoked") }}</option>
                </select>
            </section>
            <section class="count-strip" :aria-label="baseT('common.keyCounts')">
                <div v-for="(count, status) in request.data.value.counts" :key="status">
                    <span>{{ statusLabel(String(status)) }}</span>
                    <strong>{{ count }}</strong>
                </div>
            </section>
            <section class="table-section">
                <UiDataTable
                    :columns="columns"
                    :rows="request.data.value.keys"
                >
                    <template #key_id="{row}">
                        <code>{{ row.key_id }}</code>
                    </template>
                    <template #plaintext="{row}">
                        <div v-if="row.plaintext" class="plaintext-cell">
                            <code>{{ row.plaintext }}</code>
                            <UiButton variant="ghost" @click="copyText(row.plaintext)">
                                {{ pageT("copy") }}
                            </UiButton>
                        </div>
                        <span v-else>{{ baseT("common.emptyValue") }}</span>
                    </template>
                    <template #team_id="{row}">
                        {{ row.team_id || baseT("common.emptyValue") }}
                    </template>
                    <template #status="{row}">
                        <span class="status" :data-status="effectiveStatus(row)">{{ statusLabel(effectiveStatus(row)) }}</span>
                    </template>
                    <template #permissions="{row}">
                        {{ row.permissions?.length ?? 0 }}
                    </template>
                    <template #expires_at="{row}">
                        {{ row.expires_at ? formatTimestamp(row.expires_at, localeName) : pageT("never") }}
                    </template>
                    <template #created_at="{row}">
                        {{ formatTimestamp(row.created_at, localeName) }}
                    </template>
                    <template #note="{row}">
                        {{ row.note || baseT("common.emptyValue") }}
                    </template>
                    <template #batch_id="{row}">
                        <div class="row-actions">
                            <UiButton
                                v-if="row.batch_id"
                                variant="ghost"
                                :disabled="mutationPending"
                                @click="openBatch(row)"
                            >
                                {{ pageT("batch") }}
                            </UiButton>
                            <UiButton
                                v-if="canManage"
                                variant="ghost"
                                :disabled="mutationPending"
                                @click="openManage(row)"
                            >
                                {{ pageT("manage") }}
                            </UiButton>
                        </div>
                    </template>
                </UiDataTable>
            </section>
            <div v-if="pageCount > 1" class="pagination">
                <UiButton variant="ghost" :disabled="page === 1" @click="previousPage">
                    {{ pageT("previousPage") }}
                </UiButton>
                <span>{{ page }} / {{ pageCount }}</span>
                <UiButton variant="ghost" :disabled="page === pageCount" @click="nextPage">
                    {{ pageT("nextPage") }}
                </UiButton>
            </div>
        </template>

        <UiDialog :open="issueDialogOpen" :title="pageT('issueTitle')" @close="closeIssue">
            <div v-if="issuedKeys.length" class="issued-panel">
                <p>{{ pageT("issued") }}</p>
                <label>{{ pageT("plaintext") }}</label>
                <textarea readonly :value="issuedKeys.join('\n')"></textarea>
                <div class="issued-actions">
                    <UiButton variant="secondary" @click="copyKeys">{{ pageT("copy") }}</UiButton>
                    <UiButton variant="secondary" @click="downloadKeys">{{ pageT("download") }}</UiButton>
                </div>
            </div>
            <div v-else class="issue-form">
                <UiInput
                    v-model="issueQuantity"
                    :aria-label="pageT('quantity')"
                    :placeholder="pageT('quantity')"
                />
                <ValidityInput v-model="issueExpiresAt" :label="pageT('issueExpires')"/>
                <PermissionTreeEditor
                    v-model="issuePermissions"
                    :grantable="canGrantNode"
                    :label="pageT('permissionList')"
                    :options="editorCatalog"
                />
            </div>
            <template #footer>
                <UiButton variant="ghost" :disabled="mutationPending" @click="closeIssue">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton
                    v-if="!issuedKeys.length"
                    variant="primary"
                    :disabled="mutationPending"
                    @click="issue"
                >
                    {{ pageT("issueConfirm") }}
                </UiButton>
            </template>
        </UiDialog>
        <UiDialog
            :open="issueReviewOpen"
            :title="pageT('issueReview')"
            @close="closeIssueReview"
        >
            <dl class="details-list">
                <div>
                    <dt>{{ pageT("issueReviewQuantity") }}</dt>
                    <dd>{{ issuePreview.quantity }}</dd>
                </div>
                <div>
                    <dt>{{ pageT("issueReviewPermissions") }}</dt>
                    <dd>
                        <ul class="permission-preview">
                            <li v-for="permission in issuePreview.permissions" :key="permission">
                                <code>{{ permission }}</code>
                            </li>
                        </ul>
                    </dd>
                </div>
                <div>
                    <dt>{{ pageT("issueReviewExpires") }}</dt>
                    <dd>{{ issuePreview.expiresAt
                        ? formatTimestamp(issuePreview.expiresAt, localeName)
                        : pageT("never") }}</dd>
                </div>
            </dl>
            <template #footer>
                <UiButton variant="ghost" :disabled="mutationPending" @click="closeIssueReview">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton variant="primary" :disabled="mutationPending" @click="confirmIssue">
                    {{ pageT("issueConfirm") }}
                </UiButton>
            </template>
        </UiDialog>
        <UiDialog :open="manageDialogOpen" :title="pageT('manageTitle')" @close="closeManage">
            <div v-if="selectedKey" class="manage-form">
                <dl class="details-list">
                    <div><dt>{{ pageT("key") }}</dt><dd><code>{{ selectedKey.key_id }}</code></dd></div>
                    <div v-if="selectedKey.plaintext">
                        <dt>{{ pageT("plaintext") }}</dt>
                        <dd class="plaintext-cell">
                            <code>{{ selectedKey.plaintext }}</code>
                            <UiButton variant="ghost" @click="copyText(selectedKey.plaintext)">
                                {{ pageT("copy") }}
                            </UiButton>
                        </dd>
                    </div>
                    <div><dt>{{ pageT("keyPermissions") }}</dt><dd>{{ selectedKey.permissions?.join(", ") || baseT("common.emptyValue") }}</dd></div>
                </dl>
                <section v-if="selectedKey.assignments?.length" class="assignment-section">
                    <h3>{{ pageT("keyAssignments") }}</h3>
                    <ul>
                        <li v-for="(assignment, index) in selectedKey.assignments" :key="String(assignment.assignment_id ?? index)">
                            <code>{{ String(assignment.node ?? baseT("common.emptyValue")) }}</code>
                            <span>· {{ pageT("assignmentEffect") }}: {{ String(assignment.effect ?? baseT("common.emptyValue")) }}</span>
                            <span>· {{ pageT("assignmentPriority") }}: {{ String(assignment.priority ?? 0) }}</span>
                            <span>· {{ pageT("assignmentSource") }}: {{ assignmentSource(assignment) }}</span>
                            <span>· {{ pageT("assignmentExpiry") }}: {{ assignmentExpiry(assignment) }}</span>
                        </li>
                    </ul>
                </section>
                <UiInput
                    v-model="manageNote"
                    :aria-label="pageT('notePlaceholder')"
                    :placeholder="pageT('notePlaceholder')"
                />
                <select v-model="manageStatus" :aria-label="pageT('status')">
                    <option
                        v-for="option in manageableStatusOptions()"
                        :key="option"
                        :value="option"
                    >
                        {{ pageT(option) }}
                    </option>
                </select>
                <ValidityInput v-model="manageExpiresAt" :label="pageT('issueExpires')"/>
            </div>
            <template #footer>
                <UiButton variant="ghost" :disabled="mutationPending" @click="closeManage">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton variant="primary" :disabled="mutationPending" @click="saveManage">
                    {{ pageT("manageConfirm") }}
                </UiButton>
            </template>
        </UiDialog>
        <UiDialog :open="batchDialogOpen" :title="pageT('batchTitle')" @close="closeBatch">
            <RequestState :phase="batchRequest.phase.value" @retry="selectedBatch && openBatch(selectedBatch)" @cancel="batchRequest.cancel" />
            <dl v-if="batchRequest.data.value" class="details-list">
                <div><dt>{{ pageT("batch") }}</dt><dd>{{ batchRequest.data.value.batch_id }}</dd></div>
                <div><dt>{{ pageT("batchQuantity") }}</dt><dd>{{ batchRequest.data.value.quantity }}</dd></div>
                <div><dt>{{ pageT("batchOwner") }}</dt><dd>{{ batchRequest.data.value.created_by }}</dd></div>
                <div><dt>{{ pageT("batchCreated") }}</dt><dd>{{ formatTimestamp(Number(batchRequest.data.value.created_at), localeName) }}</dd></div>
                <div>
                    <dt>{{ pageT("batchPermissions") }}</dt>
                    <dd>
                        <ul class="permission-preview">
                            <li v-for="permission in (batchRequest.data.value.permissions as string[] | undefined) ?? []" :key="permission">
                                <code>{{ permission }}</code>
                            </li>
                        </ul>
                    </dd>
                </div>
                <div><dt>{{ pageT("batchCounts") }}</dt><dd>{{ JSON.stringify(batchRequest.data.value.counts) }}</dd></div>
            </dl>
            <template #footer>
                <UiButton variant="ghost" @click="closeBatch">{{ pageT("close") }}</UiButton>
            </template>
        </UiDialog>
    </div>
</template>

<style scoped>
.keys-page {
    display: grid;
    gap: 24px;
}

.count-strip {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
}

.count-strip div {
    display: grid;
    gap: 6px;
    min-width: 112px;
    padding: 12px 14px;
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 8px;
    background: #171a21;
}

.count-strip span {
    color: #9aa6b2;
    font-size: 12px;
}

.count-strip strong {
    font-size: 20px;
}

.table-section {
    min-width: 0;
}

.filter-section {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 180px;
    gap: 10px;
}

.issue-form,
.issued-panel {
    display: grid;
    gap: 12px;
}

.issue-form textarea,
.issued-panel textarea {
    width: 100%;
    min-height: 120px;
    padding: 10px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    resize: vertical;
    background: #101216;
    color: #edf1f7;
}

.issued-panel label {
    color: #9aa6b2;
    font-size: 12px;
}

.issued-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}

.manage-form {
    display: grid;
    gap: 12px;
}

.plaintext-cell {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
}

.assignment-section {
    display: grid;
    gap: 10px;
}

.assignment-section h3 {
    margin: 0;
    font-size: 14px;
}

.assignment-section ul {
    display: grid;
    gap: 8px;
    margin: 0;
    padding-left: 18px;
}

.assignment-section li {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
}

.permission-preview {
    display: grid;
    gap: 6px;
    margin: 0;
    padding-left: 18px;
}

.manage-form select {
    min-height: 40px;
    padding: 0 10px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    background: #101216;
    color: #edf1f7;
}

.pagination {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 12px;
    color: #9aa6b2;
}

code {
    color: #c8d5ff;
}

.status[data-status="active"] {
    color: #78d88c;
}

.status[data-status="redeemed"],
.status[data-status="expired"],
.status[data-status="revoked"] {
    color: #ffcb73;
}

@media (max-width: 520px) {
    .filter-section {
        grid-template-columns: 1fr;
    }
}
</style>
