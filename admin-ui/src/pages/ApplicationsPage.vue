<script lang="ts" setup>
import {computed, onMounted, ref} from "vue";
import {transport, type TransportResponse} from "../api/transport";
import {RequestFailure, responseFailure, type RequestPhase} from "../composables/requestState";
import {useAuthorization} from "../composables/useAuthorization";
import {useRequest} from "../composables/useRequest";
import UiDataTable from "../components/UiDataTable.vue";
import RequestState from "../components/RequestState.vue";
import {formatTimestamp} from "../composables/time";
import {useLocale} from "../i18n";
import UiDialog from "../components/UiDialog.vue";
import UiButton from "../components/UiButton.vue";
import UiInput from "../components/UiInput.vue";
import {useToast} from "../composables/useToast";
import PageHeader from "../components/PageHeader.vue";
import {useWorkspaceContext} from "../composables/useWorkspaceContext";

type Application = {
    application_id: string;
    applicant_user_id?: string;
    name: string;
    // 批准时该 Team 使用的 id（由名称派生，待审期间同名只允许一份）。
    team_id?: string;
    description?: string;
    status?: string;
    created_at?: number;
};

const messages = {
    zh: {
        approved: "已批准",
        rejected: "已拒绝",
        title: "Team 申请",
        description: "查看等待系统管理员处理的 Team 申请。",
        refresh: "刷新",
        name: "名称",
        applicant: "申请人",
        status: "状态",
        created: "申请时间",
        actions: "操作",
        approve: "批准",
        review: "申请预览",
        reviewApplicant: "申请人",
        reviewTeamId: "Team id",
        reviewDescription: "描述",
        reviewSubmitted: "申请时间",
        reject: "拒绝",
        rejectTitle: "拒绝 Team 申请",
        rejectReason: "拒绝原因",
        submitReject: "确认拒绝",
        applicationId: "申请 ID",
        close: "关闭",
        create: "提交申请",
        createTitle: "提交 Team 申请",
        createConfirm: "提交",
        namePlaceholder: "Team 名称",
        descriptionPlaceholder: "描述",
        createdToast: "Team 申请已提交。",
        pending: "待审核",
        permission: "没有查看 Team 申请的权限。",
        invalid: "Team 申请响应格式无效。",
        requiredFields: "Team 名称不能为空。",
    },
    en: {
        title: "Team applications",
        description: "Review Team applications awaiting system administrator action.",
        refresh: "Refresh",
        name: "Name",
        applicant: "Applicant",
        status: "Status",
        created: "Submitted",
        actions: "Actions",
        approve: "Approve",
        review: "Application preview",
        reviewApplicant: "Applicant",
        reviewTeamId: "Team id",
        reviewDescription: "Description",
        reviewSubmitted: "Submitted",
        reviewOwner: "Owner",
        reviewTemplates: "Cloned templates",
        reviewResources: "Registered resources",
        reviewWorkspace: "Workspace option",
        reject: "Reject",
        rejectTitle: "Reject Team application",
        rejectReason: "Rejection reason",
        submitReject: "Confirm rejection",
        applicationId: "Application ID",
        close: "Close",
        create: "Submit application",
        createTitle: "Submit Team application",
        createConfirm: "Submit",
        namePlaceholder: "Team name",
        descriptionPlaceholder: "Description",
        createdToast: "Team application submitted.",
        pending: "Pending",
        approved: "Approved",
        rejected: "Rejected",
        permission: "You do not have permission to view Team applications.",
        invalid: "The Team applications response is invalid.",
        requiredFields: "A Team name is required.",
    },
} as const;

const zhExtra = {
    reviewOwner: "Owner",
    reviewTemplates: "将克隆的模板",
    reviewResources: "将注册的资源",
    reviewWorkspace: "工作区选项",
} as const;

const {locale, t: baseT} = useLocale();
const authorization = useAuthorization();
const workspaceContext = useWorkspaceContext();
type ApplicationPage = {
    items: Application[];
    total: number;
    limit: number;
    offset: number;
};

const request = useRequest<ApplicationPage>();
const toast = useToast();
const canRead = computed(() => authorization.can("system.team_applications.read"));
const canCreate = computed(() => authorization.can("system.team_applications.create"));
const canApprove = computed(() => authorization.can("system.team_applications.approve"));
const canReject = computed(() => authorization.can("system.team_applications.reject"));
const selectedApplication = ref<Application | null>(null);
const rejectionReason = ref("");
const mutationPending = ref(false);
const mutationError = ref<string | null>(null);
const approvalConfirmation = ref<Application | null>(null);
const approvalToken = ref("");
const approvalPreview = ref<Record<string, unknown> | null>(null);
const rejectDialogOpen = computed(() => selectedApplication.value !== null);
const createDialogOpen = ref(false);
const createName = ref("");
const createDescription = ref("");
const page = ref(1);
const pageSize = 50;
const phase = computed<RequestPhase>(() => {
    if (authorization.phase.value !== "success") return authorization.phase.value;
    if (!canRead.value && !canCreate.value) return "forbidden";
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

function parseApplications(response: TransportResponse): ApplicationPage {
    const failure = responseFailure(response);
    if (failure) throw failure;
    const values = response.data?.applications;
    if (!Array.isArray(values)) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    const items = values.filter((value): value is Application =>
        Boolean(value)
        && typeof value === "object"
        && typeof (value as Record<string, unknown>).application_id === "string"
        && typeof (value as Record<string, unknown>).name === "string",
    );
    const total = Number(response.data?.total ?? items.length);
    const limit = Number(response.data?.limit ?? pageSize);
    const offset = Number(response.data?.offset ?? 0);
    if (!Number.isInteger(total) || total < 0 || !Number.isInteger(limit)
        || limit < 1 || !Number.isInteger(offset) || offset < 0) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    return {items, total, limit, offset};
}

function statusLabel(status?: string) {
    if (status === "approved") return pageT("approved");
    if (status === "rejected") return pageT("rejected");
    return pageT("pending");
}

async function load() {
    try {
        await authorization.refresh(null);
        if (!canRead.value) return;
        await request.run(async (signal) => {
            const response = await transport.request({
                action: "read",
                node: "system.team_applications",
                data: {
                    limit: pageSize,
                    offset: (page.value - 1) * pageSize,
                },
            });
            if (signal.aborted) {
                throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
            }
            return parseApplications(response);
        }, {isEmpty: (result) => result.items.length === 0});
    } catch {
        // Request state already contains the classified failure.
    }
}

async function approve(application: Application) {
    if (!canApprove.value || mutationPending.value) return;
    approvalConfirmation.value = application;
    approvalPreview.value = null;
    approvalToken.value = "";
    mutationPending.value = true;
    mutationError.value = null;
    try {
        const confirmation = await transport.request({
            action: "confirm",
            node: "system.team_applications",
            data: {application_id: application.application_id, operation: "approve"},
        });
        const confirmationFailure = responseFailure(confirmation);
        if (confirmationFailure) throw confirmationFailure;
        const token = confirmation.data?.confirmation_token;
        if (typeof token !== "string" || !token) {
            throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
        }
        approvalToken.value = token;
        approvalPreview.value = confirmation.data?.preview && typeof confirmation.data.preview === "object"
            ? confirmation.data.preview as Record<string, unknown>
            : null;
    } catch (cause) {
        mutationError.value = cause instanceof Error ? cause.message : String(cause);
        toast.push(mutationError.value, "error");
    } finally {
        mutationPending.value = false;
    }
}

function closeApprovalConfirmation() {
    if (mutationPending.value) return;
    approvalConfirmation.value = null;
    approvalPreview.value = null;
    approvalToken.value = "";
}

async function confirmApprove() {
    const application = approvalConfirmation.value;
    if (!application || !canApprove.value || mutationPending.value) return;
    mutationPending.value = true;
    mutationError.value = null;
    try {
        if (!approvalToken.value) {
            throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
        }
        const response = await transport.request({
            action: "approve",
            node: "system.team_applications",
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {application_id: application.application_id, confirmation_token: approvalToken.value},
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        approvalConfirmation.value = null;
        approvalPreview.value = null;
        approvalToken.value = "";
        toast.push(`${application.name}: ${pageT("approve")}`, "success");
        await load();
        await workspaceContext.loadOptions({preserveSelection: true});
        await authorization.refresh(null);
    } catch (cause) {
        mutationError.value = cause instanceof Error ? cause.message : String(cause);
        toast.push(mutationError.value, "error");
    } finally {
        mutationPending.value = false;
    }
}

function openReject(application: Application) {
    if (!canReject.value || mutationPending.value) return;
    selectedApplication.value = application;
    rejectionReason.value = "";
    mutationError.value = null;
}

function closeReject() {
    if (mutationPending.value) return;
    selectedApplication.value = null;
    mutationError.value = null;
}

async function reject() {
    const application = selectedApplication.value;
    if (!application || !canReject.value || mutationPending.value) return;
    if (!rejectionReason.value.trim()) {
        mutationError.value = pageT("rejectReason");
        toast.push(mutationError.value, "error");
        return;
    }
    mutationPending.value = true;
    mutationError.value = null;
    try {
        const confirmation = await transport.request({
            action: "confirm",
            node: "system.team_applications",
            data: {application_id: application.application_id, operation: "reject"},
        });
        const confirmationFailure = responseFailure(confirmation);
        if (confirmationFailure) throw confirmationFailure;
        const token = confirmation.data?.confirmation_token;
        if (typeof token !== "string" || !token) {
            throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
        }
        const response = await transport.request({
            action: "reject",
            node: "system.team_applications",
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {
                application_id: application.application_id,
                reason: rejectionReason.value.trim(),
                confirmation_token: token,
            },
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        selectedApplication.value = null;
        toast.push(`${application.name}: ${pageT("reject")}`, "success");
        await load();
        await workspaceContext.loadOptions({preserveSelection: true});
        await authorization.refresh(null);
    } catch (cause) {
        mutationError.value = cause instanceof Error ? cause.message : String(cause);
        toast.push(mutationError.value, "error");
    } finally {
        mutationPending.value = false;
    }
}

function openCreate() {
    if (!canCreate.value || mutationPending.value) return;
    createDialogOpen.value = true;
    mutationError.value = null;
}

function closeCreate() {
    if (mutationPending.value) return;
    createDialogOpen.value = false;
}

async function createApplication() {
    if (!canCreate.value || mutationPending.value) return;
    const name = createName.value.trim();
    if (!name) {
        mutationError.value = pageT("requiredFields");
        toast.push(mutationError.value, "error");
        return;
    }
    mutationPending.value = true;
    mutationError.value = null;
    try {
        const response = await transport.request({
            action: "create",
            node: "system.team_applications",
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {
                name,
                description: createDescription.value.trim(),
            },
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        createDialogOpen.value = false;
        createName.value = "";
        createDescription.value = "";
        toast.push(pageT("createdToast"), "success");
        if (canRead.value) await load();
    } catch (cause) {
        mutationError.value = cause instanceof Error ? cause.message : String(cause);
        toast.push(mutationError.value, "error");
    } finally {
        mutationPending.value = false;
    }
}

onMounted(() => {
    void load();
});

const pageCount = computed(() => Math.max(
    1,
    Math.ceil((request.data.value?.total ?? 0) / pageSize),
));

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
    <div class="applications-page">
        <PageHeader
            :eyebrow="pageT('title')"
            :title="pageT('title')"
            :description="pageT('description')"
            :refresh-label="pageT('refresh')"
            :refreshing="request.loading.value"
            @refresh="load"
        >
            <template #actions>
                <UiButton v-if="canCreate" variant="primary" :disabled="mutationPending" @click="openCreate">
                    {{ pageT("create") }}
                </UiButton>
            </template>
        </PageHeader>

        <RequestState
            :phase="phase"
            :message="phase === 'forbidden' ? pageT('permission') : null"
            @retry="load"
            @cancel="request.cancel"
        />

        <section v-if="request.data.value && phase !== 'forbidden'" class="table-section">
            <UiDataTable
                :columns="[
                    {key: 'name', label: pageT('name')},
                    {key: 'applicant_user_id', label: pageT('applicant')},
                    {key: 'status', label: pageT('status')},
                    {key: 'created_at', label: pageT('created')},
                    {key: 'application_id', label: pageT('actions')},
                ]"
                :rows="request.data.value.items"
            >
                <template #name="{row}">
                    <strong>{{ row.name }}</strong>
                    <span class="secondary-line">{{ row.team_id || row.application_id }}</span>
                </template>
                <template #applicant_user_id="{row}">
                    {{ row.applicant_user_id || baseT("common.emptyValue") }}
                </template>
                <template #status="{row}">
                    <span class="status" :data-status="row.status">
                        {{ statusLabel(row.status) }}
                    </span>
                </template>
                <template #created_at="{row}">
                    {{ formatTimestamp(row.created_at, localeName) }}
                </template>
                <template #application_id="{row}">
                    <div class="row-actions">
                        <UiButton
                            v-if="canApprove && row.status === 'pending'"
                            variant="primary"
                            :disabled="mutationPending"
                            @click="approve(row)"
                        >
                            {{ pageT("approve") }}
                        </UiButton>
                        <UiButton
                            v-if="canReject && row.status === 'pending'"
                            variant="ghost"
                            :disabled="mutationPending"
                            @click="openReject(row)"
                        >
                            {{ pageT("reject") }}
                        </UiButton>
                    </div>
                </template>
            </UiDataTable>
            <div v-if="pageCount > 1" class="pagination">
                <UiButton variant="ghost" :disabled="page === 1" @click="previousPage">
                    {{ baseT("common.previousPage") }}
                </UiButton>
                <span>{{ page }} / {{ pageCount }}</span>
                <UiButton variant="ghost" :disabled="page === pageCount" @click="nextPage">
                    {{ baseT("common.nextPage") }}
                </UiButton>
            </div>
        </section>

        <UiDialog
            :open="rejectDialogOpen"
            :title="pageT('rejectTitle')"
            @close="closeReject"
        >
            <p v-if="selectedApplication" class="dialog-target">
                {{ selectedApplication.name }}
            </p>
            <p v-if="selectedApplication" class="dialog-subline">
                {{ pageT("applicationId") }}: <code>{{ selectedApplication.application_id }}</code>
            </p>
            <UiInput
                v-model="rejectionReason"
                :aria-label="pageT('rejectReason')"
                :placeholder="pageT('rejectReason')"
            />
            <p v-if="mutationError" class="mutation-error">{{ mutationError }}</p>
            <template #footer>
                <UiButton variant="ghost" :disabled="mutationPending" @click="closeReject">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton
                    variant="primary"
                    :disabled="mutationPending || !rejectionReason.trim()"
                    @click="reject"
                >
                    {{ pageT("submitReject") }}
                </UiButton>
            </template>
        </UiDialog>
        <UiDialog
            :open="approvalConfirmation !== null"
            :title="pageT('review')"
            @close="closeApprovalConfirmation"
        >
            <dl v-if="approvalConfirmation" class="review-list">
                <div>
                    <dt>{{ pageT("name") }}</dt>
                    <dd>{{ approvalConfirmation.name }}</dd>
                </div>
                <div>
                    <dt>{{ pageT("reviewApplicant") }}</dt>
                    <dd>{{ approvalConfirmation.applicant_user_id || baseT("common.emptyValue") }}</dd>
                </div>
                <div>
                    <dt>{{ pageT("reviewTeamId") }}</dt>
                    <dd>{{ approvalConfirmation.team_id || baseT("common.emptyValue") }}</dd>
                </div>
                <div>
                    <dt>{{ pageT("reviewDescription") }}</dt>
                    <dd>{{ approvalConfirmation.description || baseT("common.emptyValue") }}</dd>
                </div>
                <div>
                    <dt>{{ pageT("reviewSubmitted") }}</dt>
                    <dd>{{ formatTimestamp(approvalConfirmation.created_at, localeName) }}</dd>
                </div>
                <div v-if="approvalPreview?.team && typeof approvalPreview.team === 'object'">
                    <dt>{{ pageT("reviewOwner") }}</dt>
                    <dd>{{ (approvalPreview.team as Record<string, unknown>).owner_user_id || baseT("common.emptyValue") }}</dd>
                </div>
                <div v-if="approvalPreview?.workspace_option && typeof approvalPreview.workspace_option === 'object'">
                    <dt>{{ pageT("reviewWorkspace") }}</dt>
                    <dd>{{ (approvalPreview.workspace_option as Record<string, unknown>).name || baseT("common.emptyValue") }}</dd>
                </div>
                <div v-if="Array.isArray(approvalPreview?.templates)">
                    <dt>{{ pageT("reviewTemplates") }}</dt>
                    <dd>
                        <ul class="review-items">
                            <li v-for="template in approvalPreview.templates" :key="String((template as Record<string, unknown>).name)">
                                {{ (template as Record<string, unknown>).name }}
                            </li>
                        </ul>
                    </dd>
                </div>
                <div v-if="Array.isArray(approvalPreview?.resources)">
                    <dt>{{ pageT("reviewResources") }}</dt>
                    <dd>
                        <ul class="review-items">
                            <li v-for="resource in approvalPreview.resources" :key="String(resource)">
                                <code>{{ resource }}</code>
                            </li>
                        </ul>
                    </dd>
                </div>
            </dl>
            <template #footer>
                <UiButton variant="ghost" :disabled="mutationPending" @click="closeApprovalConfirmation">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton variant="primary" :disabled="mutationPending" @click="confirmApprove">
                    {{ pageT("approve") }}
                </UiButton>
            </template>
        </UiDialog>
        <UiDialog :open="createDialogOpen" :title="pageT('createTitle')" @close="closeCreate">
            <div class="create-form">
                <label>{{ pageT("name") }}
                <UiInput
                    v-model="createName"
                    :aria-label="pageT('namePlaceholder')"
                    :placeholder="pageT('namePlaceholder')"
                />
                </label>
                <label>{{ pageT("descriptionPlaceholder") }}
                <textarea
                    v-model="createDescription"
                    :aria-label="pageT('descriptionPlaceholder')"
                    :placeholder="pageT('descriptionPlaceholder')"
                ></textarea></label>
                <p v-if="mutationError" class="mutation-error">{{ mutationError }}</p>
            </div>
            <template #footer>
                <UiButton variant="ghost" :disabled="mutationPending" @click="closeCreate">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton variant="primary" :disabled="mutationPending" @click="createApplication">
                    {{ pageT("createConfirm") }}
                </UiButton>
            </template>
        </UiDialog>
    </div>
</template>

<style scoped>
.applications-page {
    display: grid;
    gap: 24px;
}

.secondary-line {
    margin: 8px 0 0;
    color: #9aa6b2;
}

.table-section {
    min-width: 0;
}

.pagination {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 12px;
    color: #9aa6b2;
}

.secondary-line {
    display: block;
    font-size: 12px;
}

.status {
    color: #ffcb73;
}

.row-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}

.dialog-target {
    margin: 0 0 6px;
    color: #c8d5ff;
}

.dialog-subline {
    margin: 0 0 14px;
    color: #9aa6b2;
    font-size: 12px;
}

.review-list {
    display: grid;
    gap: 12px;
    margin: 0;
}

.review-list div {
    display: grid;
    gap: 4px;
}

.review-list dt {
    color: #9aa6b2;
    font-size: 12px;
}

.review-list dd {
    margin: 0;
    overflow-wrap: anywhere;
}

.review-items {
    display: grid;
    gap: 4px;
    margin: 0;
    padding-left: 18px;
}

.mutation-error {
    color: #ff9b9b;
}

.create-form {
    display: grid;
    gap: 12px;
}

.create-form textarea {
    width: 100%;
    min-height: 120px;
    padding: 10px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    resize: vertical;
    background: #101216;
    color: #edf1f7;
}

@media (max-width: 520px) {
}
</style>
