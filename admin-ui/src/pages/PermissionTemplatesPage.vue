<script lang="ts" setup>
import {computed, onMounted, ref, watch} from "vue";
import {transport, type TransportResponse} from "../api/transport";
import {RequestFailure, responseFailure, type RequestPhase} from "../composables/requestState";
import {useAuthorization} from "../composables/useAuthorization";
import {useRequest} from "../composables/useRequest";
import {useWorkspaceContext} from "../composables/useWorkspaceContext";
import UiDataTable from "../components/UiDataTable.vue";
import RequestState from "../components/RequestState.vue";
import {useLocale} from "../i18n";
import UiButton from "../components/UiButton.vue";
import UiDialog from "../components/UiDialog.vue";
import UiInput from "../components/UiInput.vue";
import PageHeader from "../components/PageHeader.vue";
import {useToast} from "../composables/useToast";
import {formatTimestamp} from "../composables/time";
import PermissionTreeEditor from "../components/PermissionTreeEditor.vue";
import {resourceGrantNode} from "../authorization/evaluator";

type PermissionTemplate = {
    template_id: string;
    name: string;
    permissions: string[];
    expires_in?: number | null;
    created_by?: string;
    created_at?: number;
    status?: "active" | "disabled";
};

const messages = {
    zh: {
        title: "权限模板",
        description: "查看当前工作区可用的权限模板和授权范围。",

        refresh: "刷新",
        name: "名称",
        permissions: "权限数量",
        expiry: "有效期",

        createdBy: "创建者",

        created: "创建时间",
        noWorkspace: "当前没有可用工作区。",

        permission: "没有查看权限模板的权限。",

        invalid: "权限模板响应格式无效。",

        permanent: "永久",
        days: "天",

        create: "新建模板",
        createTitle: "新建权限模板",
        createConfirm: "创建",
        close: "关闭",
        namePlaceholder: "模板名称",
        permissionPlaceholder: "权限节点",

        expiresPlaceholder: "有效期天数，可留空",

        createdToast: "模板已创建",

        status: "状态",

        active: "启用",
        disabled: "禁用",
        edit: "编辑",
        editTitle: "编辑权限模板",
        enable: "启用",
        disable: "禁用",
        delete: "删除",
        deleteHint: "删除保留审计记录，模板被禁用而非物理移除。",

        templateTeamNote: "固定 Template Team 是保留实例，不能物理删除；删除操作仅禁用并保留审计记录。",

        nameRequired: "模板名称不能为空。",

        expiresRequired: "有效期天数必须是正整数。",

        editConfirm: "保存修改",
        previewChanges: "预览变更",
        permissionsAdded: "新增权限",
        permissionsRemoved: "移除权限",
        expiryChanged: "有效期变化",

        statusChanged: "状态变化",

        affectedUsers: "影响用户",
        accessImpact: "访问影响",
        noChanges: "没有权限变化",
        yes: "是",

        no: "否",

    },
    en: {
        title: "Permission templates",
        description: "Review permission templates and their authorization scope for this workspace.",
        refresh: "Refresh",
        name: "Name",
        permissions: "Permissions",
        expiry: "Expiry",
        createdBy: "Created by",
        created: "Created",
        noWorkspace: "No workspace is available.",
        permission: "You do not have permission to view permission templates.",
        invalid: "The permission templates response is invalid.",
        permanent: "Permanent",
        days: "days",
        create: "Create template",
        createTitle: "Create permission template",
        createConfirm: "Create",
        close: "Close",
        namePlaceholder: "Template name",
        permissionPlaceholder: "Permission nodes",
        expiresPlaceholder: "Expiry in days, optional",
        createdToast: "Template created",
        status: "Status",
        active: "Active",
        disabled: "Disabled",
        edit: "Edit",
        editTitle: "Edit permission template",
        enable: "Enable",
        disable: "Disable",
        delete: "Delete",
        deleteHint: "Deletion keeps audit records; the template is disabled, not physically removed.",
        templateTeamNote: "The fixed Template Team is a retained instance and cannot be physically deleted; deletion only disables it and keeps audit records.",
        nameRequired: "A template name is required.",
        expiresRequired: "Expiry in days must be a positive integer.",
        editConfirm: "Save changes",
        previewChanges: "Preview changes",
        permissionsAdded: "Permissions added",
        permissionsRemoved: "Permissions removed",
        expiryChanged: "Expiry changed",
        statusChanged: "Status changed",
        affectedUsers: "Affected users",
        accessImpact: "Access impact",
        noChanges: "No permission changes",
        yes: "Yes",
        no: "No",
    },
} as const;

const {locale, t: baseT} = useLocale();
const authorization = useAuthorization();

// 模板写入按资源级委派节点做 require_grantable（后端 resource_grant_node）：没拿到该资源
// `.grant` 的账户选中后提交必然失败，这里提前判定。
function canGrantNode(node: string) {
    const grantNode = resourceGrantNode(node);
    return authorization.can(grantNode) && authorization.canGrant(grantNode);
}
const {selected} = useWorkspaceContext();
type TemplatePage = {
    items: PermissionTemplate[];
    catalog: string[];
    total: number;
    limit: number;
    offset: number;
};

const request = useRequest<TemplatePage>();
const toast = useToast();
const permissionFailure = ref<RequestFailure | null>(null);
const createOpen = ref(false);
const createName = ref("");
const catalog = ref<string[]>([]);
const createPermissionSelection = ref<Record<string, string>>({});
const createExpires = ref("");
const mutationPending = ref(false);
const mutationError = ref<string | null>(null);
const editOpen = ref(false);
const editTemplate = ref<PermissionTemplate | null>(null);
const editName = ref("");
const editPermissions = ref<Record<string, string>>({});
const editExpires = ref("");
const templatePreviewDiff = ref<Record<string, unknown> | null>(null);
const page = ref(1);
const pageSize = 50;
const localeName = computed(() => locale.value === "zh" ? "zh-CN" : "en-US");
const workspace = computed(() => selected.value);
// Team 工作区只展示本 Team 的节点：目录是全平台的，不裁剪就会把别的 Team 与系统节点
// 摆在面前，看起来"可以分发 Team 之外的权限"。
const editorCatalog = computed(() => {
    const current = workspace.value;
    return current && current.workspace_kind !== "system"
        ? catalog.value.filter((value) => value.startsWith(`team.${current.team_id}.`))
        : catalog.value;
});
const node = computed(() => workspace.value
    ? `team.${workspace.value.team_id}.permission_templates`
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

function pageT(key: string) {
    const value = key.split(".").reduce<unknown>(
        (current, part) => current && typeof current === "object"
            ? (current as Record<string, unknown>)[part]
            : undefined,
        messages[locale.value],
    );
    return String(value ?? key);
}

function parseTemplates(response: TransportResponse): TemplatePage {
    const failure = responseFailure(response);
    if (failure) throw failure;
    const values = response.data?.templates;
    if (!Array.isArray(values)) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    const items = values.filter((value): value is PermissionTemplate =>
        Boolean(value)
        && typeof value === "object"
        && typeof (value as Record<string, unknown>).template_id === "string"
        && typeof (value as Record<string, unknown>).name === "string"
        && Array.isArray((value as Record<string, unknown>).permissions),
    );
    const total = Number(response.data?.total ?? items.length);
    const limit = Number(response.data?.limit ?? pageSize);
    const offset = Number(response.data?.offset ?? 0);
    if (!Number.isInteger(total) || total < 0 || !Number.isInteger(limit)
        || !Number.isInteger(offset) || limit < 1 || offset < 0) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    const catalogValues = response.data?.catalog;
    if (!Array.isArray(catalogValues) || !catalogValues.every((value) => typeof value === "string")) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    return {items, catalog: catalogValues, total, limit, offset};
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
                },
            });
            if (signal.aborted) {
                throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
            }
            const parsed = parseTemplates(response);
            // 目录随列表一起返回，编辑器要用它。
            catalog.value = parsed.catalog;
            return parsed;
        }, {isEmpty: (result) => result.items.length === 0});
    } catch (cause) {
        if (cause instanceof RequestFailure && cause.code === "permission_denied") {
            permissionFailure.value = cause;
        }
    }
}

function expiryLabel(value?: number | null) {
    return value === null || value === undefined
        ? pageT("permanent")
        : `${value} ${pageT("days")}`;
}

function openCreate() {
    if (!canManage.value || mutationPending.value) return;
    mutationError.value = null;
    createPermissionSelection.value = {};
    createOpen.value = true;
}

function closeCreate() {
    if (mutationPending.value) return;
    createOpen.value = false;
}

async function createTemplate() {
    if (!canManage.value || mutationPending.value || !node.value) return;
    const name = createName.value.trim();
    if (!name) {
        mutationError.value = pageT("nameRequired");
        return;
    }
    const expiresRaw = createExpires.value.trim();
    if (expiresRaw && (!Number.isInteger(Number.parseInt(expiresRaw, 10)) || Number.parseInt(expiresRaw, 10) < 1)) {
        mutationError.value = pageT("expiresRequired");
        return;
    }
    const permissions = Object.keys(createPermissionSelection.value);
    mutationPending.value = true;
    mutationError.value = null;
    try {
        const response = await transport.request({
            action: "create",
            node: node.value,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {
                name,
                permissions,
                expires_in: expiresRaw
                    ? Number.parseInt(expiresRaw, 10)
                    : null,
            },
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        createOpen.value = false;
        createName.value = "";
        createPermissionSelection.value = {};
        createExpires.value = "";
        toast.push(pageT("createdToast"), "success");
        await load();
    } catch (cause) {
        mutationError.value = cause instanceof Error ? cause.message : String(cause);
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
        mutationPending.value = false;
    }
}

function openEdit(template: PermissionTemplate) {
    if (!canManage.value || mutationPending.value) return;
    editTemplate.value = template;
    editName.value = template.name;
    editPermissions.value = Object.fromEntries(
        template.permissions.map((node: string) => [node, "allow"]),
    );
    editExpires.value = template.expires_in === null || template.expires_in === undefined
        ? ""
        : String(template.expires_in);
    mutationError.value = null;
    editOpen.value = true;
}

function closeEdit() {
    if (mutationPending.value) return;
    editOpen.value = false;
    editTemplate.value = null;
    templatePreviewDiff.value = null;
}

function editPayload(template: PermissionTemplate) {
    return {
        template_id: template.template_id,
        name: editName.value.trim(),
        permissions: Object.keys(editPermissions.value),
        expires_in: editExpires.value.trim()
            ? Number.parseInt(editExpires.value, 10)
            : null,
    };
}

async function previewTemplateEdit() {
    const template = editTemplate.value;
    if (!template || !canManage.value || mutationPending.value || !node.value) return null;
    const preview = await transport.request({
        action: "manage",
        node: node.value,
        data: {...editPayload(template), preview: true},
    });
    const previewFailure = responseFailure(preview);
    if (previewFailure) throw previewFailure;
    const diff = preview.data?.diff;
    if (!diff || typeof diff !== "object") {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    templatePreviewDiff.value = diff as Record<string, unknown>;
    return templatePreviewDiff.value;
}

async function requestTemplatePreview() {
    try {
        mutationError.value = null;
        await previewTemplateEdit();
    } catch (cause) {
        mutationError.value = cause instanceof Error ? cause.message : String(cause);
        toast.push(mutationError.value, "error");
    }
}

function diffList(key: string) {
    const value = templatePreviewDiff.value?.[key];
    return Array.isArray(value)
        ? value.filter((item): item is string => typeof item === "string")
        : [];
}

function diffBool(key: string) {
    return templatePreviewDiff.value?.[key] === true;
}

function templateAccessImpact() {
    const value = templatePreviewDiff.value?.access_impact;
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

async function requestTemplateOperation(
    operation: "update" | "enable" | "disable" | "delete",
    template: PermissionTemplate | null = editTemplate.value,
) {
    if (!canManage.value || mutationPending.value || !node.value || !template) return;

    if (operation === "update") {
        const name = editName.value.trim();
        if (!name) {
            mutationError.value = pageT("nameRequired");
            return;
        }
        const expiresRaw = editExpires.value.trim();
        if (expiresRaw
            && (!Number.isInteger(Number.parseInt(expiresRaw, 10))
                || Number.parseInt(expiresRaw, 10) < 1)) {
            mutationError.value = pageT("expiresRequired");
            return;
        }
    }

    mutationPending.value = true;
    mutationError.value = null;
    try {
        let updateData: Record<string, unknown> | null = null;
        let requiresConfirmation = false;
        if (operation === "update") {
            updateData = editPayload(template);
            const diff = await previewTemplateEdit();
            requiresConfirmation = diff?.requires_confirmation === true;
        }
        let confirmationToken: string | undefined;
        if (requiresConfirmation || operation === "disable" || operation === "delete") {
            const confirmation = await transport.request({
                action: operation === "disable" || operation === "delete"
                    ? "confirm"
                    : "manage",
                node: node.value,
                data: {
                    template_id: template.template_id,
                    ...(operation === "update"
                        ? {
                            mode: "confirm",
                            confirmation_action: "permission_template.update",
                        }
                        : {}),
                },
            });
            const confirmationFailure = responseFailure(confirmation);
            if (confirmationFailure) throw confirmationFailure;
            const token = confirmation.data?.confirmation_token;
            if (typeof token !== "string" || !token) {
                throw new RequestFailure(
                    pageT("invalid"),
                    "validation",
                    "invalid_response",
                );
            }
            confirmationToken = token;
        }

        const data: Record<string, unknown> = {
            template_id: template.template_id,
        };
        if (operation === "update" && updateData) {
            delete updateData.preview;
            Object.assign(data, updateData);
        } else {
            data.status = operation === "enable" ? "active" : "disabled";
            if (operation === "delete") {
                data.delete = true;
            }
        }
        if (confirmationToken) {
            data.confirmation_token = confirmationToken;
        }

        const response = await transport.request({
            action: "manage",
            node: node.value,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data,
        });
        const failure = responseFailure(response);
        if (failure) throw failure;

        if (operation === "update") {
            editOpen.value = false;
            editTemplate.value = null;
            templatePreviewDiff.value = null;
        }
        toast.push(
            operation === "update"
                ? pageT("editConfirm")
                : pageT(operation),
            "success",
        );
        await load();
    } catch (cause) {
        mutationError.value = cause instanceof Error ? cause.message : String(cause);
        toast.push(mutationError.value, "error");
    } finally {
        mutationPending.value = false;
    }
}

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

onMounted(load);
watch(() => workspace.value?.team_id, (current, previous) => {
    if (current === previous) return;
    page.value = 1;
    void load();
});
</script>

<template>
    <div class="templates-page">
        <PageHeader
            :eyebrow="pageT('title')"
            :title="pageT('title')"
            :description="pageT('description')"
            :refresh-label="pageT('refresh')"
            :refreshing="request.loading.value"
            @refresh="load"
        >
            <template #actions>
                <UiButton v-if="canManage" variant="primary" :disabled="mutationPending" @click="openCreate">
                    {{ pageT("create") }}
                </UiButton>
            </template>
        </PageHeader>

        <p v-if="workspace?.team_id === 'template'" class="template-team-note">
            {{ pageT("templateTeamNote") }}
        </p>

        <RequestState
            :phase="phase"
            :message="!workspace ? pageT('noWorkspace') : phase === 'forbidden' ? pageT('permission') : null"
            @retry="load"
            @cancel="request.cancel"
        />

        <section v-if="request.data.value && phase !== 'forbidden'" class="table-section">
            <UiDataTable
                :columns="[
                    {key: 'name', label: pageT('name')},
                    {key: 'status', label: pageT('status')},
                    {key: 'permissions', label: pageT('permissions')},
                    {key: 'expires_in', label: pageT('expiry')},
                    {key: 'created_by', label: pageT('createdBy')},
                    {key: 'created_at', label: pageT('created')},
                    {key: 'template_id', label: pageT('edit')},
                ]"
                :rows="request.data.value.items"
            >
                <template #name="{row}">
                    <strong>{{ row.name }}</strong>
                    <span class="secondary-line">{{ row.template_id }}</span>
                </template>
                <template #status="{row}">
                    <span class="status" :data-status="row.status ?? 'active'">
                        {{ row.status === "disabled" ? pageT("disabled") : pageT("active") }}
                    </span>
                </template>
                <template #permissions="{row}">
                    {{ row.permissions.length }}
                </template>
                <template #expires_in="{row}">
                    {{ expiryLabel(row.expires_in) }}
                </template>
                <template #created_by="{row}">
                    {{ row.created_by || baseT("common.emptyValue") }}
                </template>
                <template #created_at="{row}">
                    {{ formatTimestamp(row.created_at, localeName) }}
                </template>
                <template #template_id="{row}">
                    <div class="row-actions">
                        <UiButton
                            v-if="canManage"
                            variant="ghost"
                            :disabled="mutationPending"
                            @click="openEdit(row)"
                        >
                            {{ pageT("edit") }}
                        </UiButton>
                        <UiButton
                            v-if="canManage && row.status === 'disabled'"
                            variant="ghost"
                            :disabled="mutationPending"
                            @click="requestTemplateOperation('enable', row)"
                        >
                            {{ pageT("enable") }}
                        </UiButton>
                        <UiButton
                            v-if="canManage && row.status !== 'disabled'"
                            variant="ghost"
                            :disabled="mutationPending"
                            @click="requestTemplateOperation('disable', row)"
                        >
                            {{ pageT("disable") }}
                        </UiButton>
                        <UiButton
                            v-if="canManage && row.status !== 'disabled'"
                            variant="ghost"
                            :disabled="mutationPending"
                            :title="pageT('deleteHint')"
                            @click="requestTemplateOperation('delete', row)"
                        >
                            {{ pageT("delete") }}
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
        <UiDialog :open="createOpen" :title="pageT('createTitle')" @close="closeCreate">
            <div class="create-form">
                <UiInput v-model="createName" :aria-label="pageT('namePlaceholder')" :placeholder="pageT('namePlaceholder')" />
                <PermissionTreeEditor
                    v-model="createPermissionSelection"
                    :grantable="canGrantNode"
                    :label="pageT('permissionPlaceholder')"
                    :options="editorCatalog"
                />
                <UiInput v-model="createExpires" :aria-label="pageT('expiresPlaceholder')" :placeholder="pageT('expiresPlaceholder')" />
                <p v-if="mutationError" class="mutation-error">{{ mutationError }}</p>
            </div>
            <template #footer>
                <UiButton variant="ghost" :disabled="mutationPending" @click="closeCreate">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton variant="primary" :disabled="mutationPending" @click="createTemplate">
                    {{ pageT("createConfirm") }}
                </UiButton>
            </template>
        </UiDialog>
        <UiDialog :open="editOpen" :title="pageT('editTitle')" @close="closeEdit">
            <div class="create-form">
                <UiInput
                    v-model="editName"
                    :aria-label="pageT('namePlaceholder')"
                    :placeholder="pageT('namePlaceholder')"
                />
                <PermissionTreeEditor
                    v-model="editPermissions"
                    :grantable="canGrantNode"
                    :label="pageT('permissionPlaceholder')"
                    :options="editorCatalog"
                />
                <UiInput
                    v-model="editExpires"
                    :aria-label="pageT('expiresPlaceholder')"
                    :placeholder="pageT('expiresPlaceholder')"
                />
                <dl v-if="templatePreviewDiff" class="diff-panel" :aria-label="pageT('accessImpact')">
                    <div>
                        <dt>{{ pageT("permissionsAdded") }}</dt>
                        <dd v-if="diffList('permissions_added').length"><code v-for="item in diffList('permissions_added')" :key="item">{{ item }}</code></dd>
                        <dd v-else>{{ pageT("noChanges") }}</dd>
                    </div>
                    <div>
                        <dt>{{ pageT("permissionsRemoved") }}</dt>
                        <dd v-if="diffList('permissions_removed').length"><code v-for="item in diffList('permissions_removed')" :key="item">{{ item }}</code></dd>
                        <dd v-else>{{ pageT("noChanges") }}</dd>
                    </div>
                    <div>
                        <dt>{{ pageT("expiryChanged") }}</dt>
                        <dd>{{ diffBool("expiry_changed") ? pageT("yes") : pageT("no") }}</dd>
                    </div>
                    <div>
                        <dt>{{ pageT("statusChanged") }}</dt>
                        <dd>{{ diffBool("status_changed") ? pageT("yes") : pageT("no") }}</dd>
                    </div>
                    <div>
                        <dt>{{ pageT("affectedUsers") }}</dt>
                        <dd v-if="diffList('affected_users').length"><code v-for="item in diffList('affected_users')" :key="item">{{ item }}</code></dd>
                        <dd v-else>{{ pageT("noChanges") }}</dd>
                    </div>
                    <div>
                        <dt>{{ pageT("accessImpact") }}</dt>
                        <dd>
                            {{ templateAccessImpact().adds_access ? pageT("permissionsAdded") : "" }}
                            {{ templateAccessImpact().removes_access ? pageT("permissionsRemoved") : "" }}
                            {{ templateAccessImpact().changes_expiry ? pageT("expiryChanged") : "" }}
                            {{ !templateAccessImpact().adds_access && !templateAccessImpact().removes_access && !templateAccessImpact().changes_expiry ? pageT("noChanges") : "" }}
                        </dd>
                    </div>
                </dl>
                <p v-if="mutationError" class="mutation-error">{{ mutationError }}</p>
            </div>
            <template #footer>
                <UiButton variant="ghost" :disabled="mutationPending" @click="closeEdit">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton variant="ghost" :disabled="mutationPending" @click="requestTemplatePreview">
                    {{ pageT("previewChanges") }}
                </UiButton>
                <UiButton variant="primary" :disabled="mutationPending" @click="requestTemplateOperation('update')">
                    {{ pageT("editConfirm") }}
                </UiButton>
            </template>
        </UiDialog>
    </div>
</template>

<style scoped>
.templates-page {
    display: grid;
    gap: 24px;
}

.secondary-line {
    margin: 8px 0 0;
    color: #9aa6b2;
}

.table-section {
    min-width: 0;
    padding: 2px 0;
}

.secondary-line {
    display: block;
    font-size: 12px;
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

.diff-panel {
    display: grid;
    gap: 10px;
    margin: 2px 0;
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

.status[data-status="active"] {
    color: #78d88c;
}

.status[data-status="disabled"] {
    color: #ffcb73;
}

.mutation-error {
    margin: 0;
    color: #ff9b9b;
}

.template-team-note {
    margin: 0;
    padding: 10px 12px;
    border: 1px solid rgba(255, 203, 115, 0.4);
    border-radius: 8px;
    background: rgba(255, 203, 115, 0.08);
    color: #ffd98a;
}

@media (max-width: 520px) {
}
</style>
