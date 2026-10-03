<script lang="ts" setup>
import {computed, onBeforeUnmount, onMounted, ref, watch} from "vue";
import {transport, type TransportResponse} from "../api/transport";
import {RequestFailure, responseFailure, type RequestPhase} from "../composables/requestState";
import {useAuthorization} from "../composables/useAuthorization";
import {useRequest} from "../composables/useRequest";
import {useWorkspaceContext} from "../composables/useWorkspaceContext";
import {formatTimestamp} from "../composables/time";
import UiDataTable from "../components/UiDataTable.vue";
import RequestState from "../components/RequestState.vue";
import {useLocale} from "../i18n";
import UiButton from "../components/UiButton.vue";
import UiDialog from "../components/UiDialog.vue";
import UiInput from "../components/UiInput.vue";
import {useToast} from "../composables/useToast";
import PageHeader from "../components/PageHeader.vue";

type AuditEvent = {
    actor: string;
    action: string;
    target: string;
    metadata: Record<string, unknown> | null;
    created_at: number;
};

type AuditPage = {
    events: AuditEvent[];
    total: number;
    limit: number;
    offset: number;
    team_id: string;
    workspace_kind: "system" | "team";
};

const messages = {
    zh: {
        title: "审计",
        description: "查看本工作区可读的审计事件，支持搜索与 CSV 导出。",
        refresh: "刷新",
        actor: "操作者",
        action: "动作",
        target: "目标",
        time: "时间",
        details: "详情",
        detailsTitle: "审计详情",
        close: "关闭",
        noWorkspace: "当前没有可用工作区。",
        permission: "没有查看审计的权限。",
        invalid: "审计响应格式无效。",
        query: "搜索操作者、动作或目标",
        actorFilter: "操作者",
        actionFilter: "动作",
        targetFilter: "目标",
        previousPage: "上一页",
        nextPage: "下一页",
        exportCsv: "导出 CSV",
        exportDone: "审计 CSV 已导出。",
        exportFailed: "审计导出失败。",
        metadata: "Metadata",
        noEvents: "暂无审计事件",
    },
    en: {
        title: "Audit",
        description: "Review audit events readable in this workspace, with search and CSV export.",
        refresh: "Refresh",
        actor: "Actor",
        action: "Action",
        target: "Target",
        time: "Time",
        details: "Details",
        detailsTitle: "Audit event details",
        close: "Close",
        noWorkspace: "No workspace is available.",
        permission: "You do not have permission to view audit.",
        invalid: "The audit response is invalid.",
        query: "Search actor, action, or target",
        actorFilter: "Actor",
        actionFilter: "Action",
        targetFilter: "Target",
        previousPage: "Previous page",
        nextPage: "Next page",
        exportCsv: "Export CSV",
        exportDone: "Audit CSV exported.",
        exportFailed: "Audit export failed.",
        metadata: "Metadata",
        noEvents: "No audit events",
    },
} as const;

const {locale, t: baseT} = useLocale();
const authorization = useAuthorization();
const request = useRequest<AuditPage>();
const toast = useToast();
const permissionFailure = ref<RequestFailure | null>(null);
const {selected} = useWorkspaceContext();
const workspace = computed(() => selected.value);
const node = computed(() => workspace.value
    ? `team.${workspace.value.team_id}.audit`
    : "");
const canRead = computed(() => node.value ? authorization.can(`${node.value}.read`) : false);
const canExport = computed(() => node.value ? authorization.can(`${node.value}.export`) : false);
const query = ref("");
const actorFilter = ref("");
const actionFilter = ref("");
const targetFilter = ref("");
const page = ref(1);
const pageSize = 50;
const localeName = computed(() => locale.value === "zh" ? "zh-CN" : "en-US");
const selectedEvent = ref<AuditEvent | null>(null);
const exportPending = ref(false);
const pageCount = computed(() => Math.max(
    1,
    Math.ceil((request.data.value?.total ?? 0) / pageSize),
));
const events = computed(() => request.data.value?.events ?? []);
const phase = computed<RequestPhase>(() => {
    if (!workspace.value) return "empty";
    if (permissionFailure.value) return "forbidden";
    if (authorization.phase.value !== "success") return authorization.phase.value;
    if (!canRead.value) return "forbidden";
    return request.phase.value;
});

function pageT(key: string) {
    const value = key.split(".").reduce<unknown>(
        (current, part) => current && typeof current === "object"
            ? (current as Record<string, unknown>)[part]
            : undefined,
        messages[locale.value],
    );
    return String(value ?? key);
}

function parseAudit(response: TransportResponse): AuditPage {
    const failure = responseFailure(response);
    if (failure) throw failure;
    const values = response.data?.events;
    if (!Array.isArray(values)) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    const events = values.filter((value): value is AuditEvent =>
        Boolean(value)
        && typeof value === "object"
        && typeof (value as Record<string, unknown>).actor === "string"
        && typeof (value as Record<string, unknown>).action === "string"
        && typeof (value as Record<string, unknown>).target === "string",
    );
    const total = Number(response.data?.total ?? events.length);
    const limit = Number(response.data?.limit ?? pageSize);
    const offset = Number(response.data?.offset ?? 0);
    if (!Number.isInteger(total) || total < 0 || !Number.isInteger(limit)
        || limit < 1 || !Number.isInteger(offset) || offset < 0) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    return {
        events,
        total,
        limit,
        offset,
        team_id: String(response.data?.team_id ?? ""),
        workspace_kind: response.data?.workspace_kind === "system" ? "system" : "team",
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
                    limit: pageSize,
                    offset: (page.value - 1) * pageSize,
                    query: query.value.trim(),
                    actor: actorFilter.value.trim(),
                    action: actionFilter.value.trim(),
                    target: targetFilter.value.trim(),
                },
            });
            if (signal.aborted) {
                throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
            }
            return parseAudit(response);
        }, {isEmpty: (result) => result.events.length === 0});
    } catch (cause) {
        if (cause instanceof RequestFailure && cause.code === "permission_denied") {
            permissionFailure.value = cause;
        }
    }
}

let filterTimer: number | null = null;

function scheduleFilterLoad() {
    page.value = 1;
    if (filterTimer !== null) window.clearTimeout(filterTimer);
    filterTimer = window.setTimeout(() => {
        filterTimer = null;
        void load();
    }, 300);
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

function eventMetadata(event: AuditEvent) {
    return event.metadata && typeof event.metadata === "object" ? event.metadata : null;
}

async function exportCsv() {
    if (!canExport.value || exportPending.value || !node.value) return;
    exportPending.value = true;
    try {
        const response = await transport.request({
            action: "export",
            node: node.value,
            data: {
                query: query.value.trim(),
                actor: actorFilter.value.trim(),
                action: actionFilter.value.trim(),
                target: targetFilter.value.trim(),
            },
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        const csvText = String(response.data?.csv ?? "");
        if (!csvText) {
            throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
        }
        const blob = new Blob([csvText], {type: "text/csv;charset=utf-8"});
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        const teamLabel = String(response.data?.team_id ?? "audit");
        anchor.download = `${teamLabel}-audit-${Date.now()}.csv`;
        anchor.click();
        URL.revokeObjectURL(url);
        toast.push(pageT("exportDone"), "success");
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
        exportPending.value = false;
    }
}

onMounted(() => void load());
onBeforeUnmount(() => {
    if (filterTimer !== null) window.clearTimeout(filterTimer);
});
watch(() => workspace.value?.team_id, (current, previous) => {
    if (current === previous) return;
    page.value = 1;
    void load();
});
watch([query, actorFilter, actionFilter, targetFilter], scheduleFilterLoad);
</script>

<template>
    <div class="audit-page">
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
                    v-if="canExport"
                    variant="primary"
                    :disabled="exportPending"
                    @click="exportCsv"
                >
                    {{ pageT("exportCsv") }}
                </UiButton>
            </template>
        </PageHeader>

        <RequestState
            :phase="phase"
            :message="!workspace ? pageT('noWorkspace') : phase === 'forbidden' ? pageT('permission') : null"
            @retry="load"
            @cancel="request.cancel"
        />

        <section v-if="request.data.value && phase !== 'forbidden'" class="audit-section">
            <div class="filter-section">
                <UiInput v-model="query" :aria-label="pageT('query')" :placeholder="pageT('query')" />
                <UiInput v-model="actorFilter" :aria-label="pageT('actorFilter')" :placeholder="pageT('actorFilter')" />
                <UiInput v-model="actionFilter" :aria-label="pageT('actionFilter')" :placeholder="pageT('actionFilter')" />
                <UiInput v-model="targetFilter" :aria-label="pageT('targetFilter')" :placeholder="pageT('targetFilter')" />
            </div>
            <UiDataTable
                :columns="[
                    {key: 'created_at', label: pageT('time')},
                    {key: 'actor', label: pageT('actor')},
                    {key: 'action', label: pageT('action')},
                    {key: 'target', label: pageT('target')},
                    {key: 'metadata', label: pageT('details')},
                ]"
                :rows="events"
            >
                <template #created_at="{row}">
                    <time>{{ formatTimestamp(row.created_at, localeName) }}</time>
                </template>
                <template #actor="{row}">
                    {{ row.actor || baseT("common.emptyValue") }}
                </template>
                <template #action="{row}">
                    <code>{{ row.action }}</code>
                </template>
                <template #target="{row}">
                    {{ row.target || baseT("common.emptyValue") }}
                </template>
                <template #metadata="{row}">
                    <UiButton variant="ghost" @click="selectedEvent = row">
                        {{ pageT("details") }}
                    </UiButton>
                </template>
            </UiDataTable>
            <div v-if="pageCount > 1" class="pagination">
                <UiButton variant="ghost" :disabled="page === 1" @click="previousPage">
                    {{ pageT("previousPage") }}
                </UiButton>
                <span>{{ page }} / {{ pageCount }}</span>
                <UiButton variant="ghost" :disabled="page === pageCount" @click="nextPage">
                    {{ pageT("nextPage") }}
                </UiButton>
            </div>
        </section>

        <UiDialog
            v-if="selectedEvent"
            :open="true"
            :title="pageT('detailsTitle')"
            @close="selectedEvent = null"
        >
            <dl class="details-list">
                <div><dt>{{ pageT("time") }}</dt><dd>{{ formatTimestamp(selectedEvent.created_at, localeName) }}</dd></div>
                <div><dt>{{ pageT("actor") }}</dt><dd>{{ selectedEvent.actor }}</dd></div>
                <div><dt>{{ pageT("action") }}</dt><dd><code>{{ selectedEvent.action }}</code></dd></div>
                <div><dt>{{ pageT("target") }}</dt><dd>{{ selectedEvent.target }}</dd></div>
                <div v-if="eventMetadata(selectedEvent)">
                    <dt>{{ pageT("metadata") }}</dt>
                    <dd><pre>{{ JSON.stringify(eventMetadata(selectedEvent), null, 4) }}</pre></dd>
                </div>
            </dl>
            <template #footer>
                <UiButton variant="ghost" @click="selectedEvent = null">{{ pageT("close") }}</UiButton>
            </template>
        </UiDialog>
    </div>
</template>

<style scoped>
.audit-page {
    display: grid;
    gap: 24px;
}

.audit-section {
    min-width: 0;
    display: grid;
    gap: 14px;
}

.filter-section {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
}

.pagination {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 10px;
    color: #9aa6b2;
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
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    padding-bottom: 10px;
}

.details-list dt {
    color: #9aa6b2;
}

.details-list dd {
    margin: 0;
    min-width: 0;
    text-align: right;
    overflow-wrap: anywhere;
}

.details-list pre {
    max-height: 260px;
    overflow: auto;
    text-align: left;
    white-space: pre-wrap;
}

@media (max-width: 860px) {
    .filter-section {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
}

@media (max-width: 520px) {
    .filter-section {
        grid-template-columns: 1fr;
    }
}
</style>