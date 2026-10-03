<script lang="ts" setup>
import {computed, onMounted} from "vue";
import {transport, type TransportResponse} from "../api/transport";
import {RequestFailure, responseFailure, type RequestPhase} from "../composables/requestState";
import {useAuthorization} from "../composables/useAuthorization";
import {useRequest} from "../composables/useRequest";
import UiDataTable from "../components/UiDataTable.vue";
import RequestState from "../components/RequestState.vue";
import {useLocale} from "../i18n";
import PageHeader from "../components/PageHeader.vue";

type Operation = {
    component: string;
    status: string;
    detail: string;
};

const messages = {
    zh: {
        title: "运行状态",
        description: "查看服务、数据库、存储和 schema 的当前状态。",
        refresh: "刷新",
        component: "组件",
        status: "状态",
        detail: "详情",
        ok: "正常",
        configured: "已配置",
        unavailable: "不可用",
        error: "异常",
        permission: "没有查看运行状态的权限。",
        invalid: "运行状态响应格式无效。",
    },
    en: {
        title: "Operations",
        description: "Review the current service, database, storage, and schema status.",
        refresh: "Refresh",
        component: "Component",
        status: "Status",
        detail: "Detail",
        ok: "OK",
        configured: "Configured",
        unavailable: "Unavailable",
        error: "Error",
        permission: "You do not have permission to view operations.",
        invalid: "The operations response is invalid.",
    },
} as const;

const {locale, t: baseT} = useLocale();
const authorization = useAuthorization();
const request = useRequest<Operation[]>();
const canRead = computed(() => authorization.can("system.operations.status.read"));
const phase = computed<RequestPhase>(() => {
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

function parseOperations(response: TransportResponse): Operation[] {
    const failure = responseFailure(response);
    if (failure) throw failure;
    const values = response.data?.items;
    if (!Array.isArray(values)) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    return values.filter((value): value is Operation =>
        Boolean(value)
        && typeof value === "object"
        && typeof (value as Record<string, unknown>).component === "string"
        && typeof (value as Record<string, unknown>).status === "string"
        && typeof (value as Record<string, unknown>).detail === "string",
    );
}

async function load() {
    try {
        await authorization.ensure(transport.selectedTeamId);
        if (!canRead.value) return;
        await request.run(async (signal) => {
            const response = await transport.request({
                action: "read",
                node: "system.operations.status",
            });
            if (signal.aborted) {
                throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
            }
            return parseOperations(response);
        }, {isEmpty: (items) => items.length === 0});
    } catch {
        // Request state already contains the classified failure.
    }
}

function statusLabel(value: string) {
    return pageT(value);
}

onMounted(() => {
    void load();
});
</script>

<template>
    <div class="operations-page">
        <PageHeader
            :eyebrow="pageT('title')"
            :title="pageT('title')"
            :description="pageT('description')"
            :refresh-label="pageT('refresh')"
            :refreshing="request.loading.value"
            @refresh="load"
        />

        <RequestState
            :phase="phase"
            :message="phase === 'forbidden' ? pageT('permission') : null"
            @retry="load"
            @cancel="request.cancel"
        />

        <section v-if="request.data.value && phase !== 'forbidden'" class="table-section">
            <UiDataTable
                :columns="[
                    {key: 'component', label: pageT('component')},
                    {key: 'status', label: pageT('status')},
                    {key: 'detail', label: pageT('detail')},
                ]"
                :rows="request.data.value"
            >
                <template #status="{row}">
                    <span class="status" :data-status="row.status">{{ statusLabel(row.status) }}</span>
                </template>
            </UiDataTable>
        </section>
    </div>
</template>

<style scoped>
.operations-page {
    display: grid;
    gap: 24px;
}

.table-section {
    min-width: 0;
}

.status[data-status="ok"],
.status[data-status="configured"] {
    color: #78d88c;
}

.status[data-status="error"],
.status[data-status="unavailable"] {
    color: #ff9b9b;
}

@media (max-width: 520px) {
}
</style>
