<script lang="ts" setup>
import {computed, onMounted, ref} from "vue";
import {useRouter} from "vue-router";
import {type TransportResponse, transport} from "../api/transport";
import {RequestFailure, responseFailure, type RequestPhase} from "../composables/requestState";
import {useAuthorization} from "../composables/useAuthorization";
import {useRequest} from "../composables/useRequest";
import {formatTimestamp} from "../composables/time";
import UiDataTable from "../components/UiDataTable.vue";
import RequestState from "../components/RequestState.vue";
import {useLocale} from "../i18n";
import UiButton from "../components/UiButton.vue";
import {useToast} from "../composables/useToast";
import UiDialog from "../components/UiDialog.vue";
import UiInput from "../components/UiInput.vue";
import PageHeader from "../components/PageHeader.vue";
import {useWorkspaceContext} from "../composables/useWorkspaceContext";

type Team = {
    team_id: string;
    name: string;
    description?: string;
    owner_user_id?: string;
    status?: string;
    member_count?: number;
    created_at?: number;
    updated_at?: number;
};

type TeamMember = {
    github_user_id: string;
    login_snapshot?: string;
    status?: string;
    source_type?: string;
    source_id?: string;
    joined_at?: number;
};

const messages = {
    zh: {
        title: "团队",
        description: "查看当前账户可见的 Team 目录和运行状态。",
        refresh: "刷新",
        team: "Team",
        status: "状态",
        owner: "Owner",
        members: "成员",
        created: "创建时间",
        updated: "更新时间",
        descriptionLabel: "描述",
        actions: "操作",
        suspend: "暂停",
        activate: "启用",
        archive: "归档",
        details: "详情",
        detailsTitle: "Team 详情",
        edit: "编辑",
        editTitle: "编辑 Team",
        namePlaceholder: "Team 名称",
        descriptionPlaceholder: "Team 描述",
        save: "保存",
        saved: "Team 已更新。",
        close: "关闭",
        invalidDetails: "Team 详情响应格式无效。",
        active: "正常",
        suspended: "已暂停",
        archived: "已归档",
        permission: "没有查看 Team 的权限。",
        invalid: "Team 响应格式无效。",
        confirmStatusTitle: "确认 Team 状态变更",
        confirmStatusBody: "确认后将立即生效：",
        statusTeam: "Team",
        statusAction: "操作",
        statusOwner: "Owner",
        statusMembers: "成员数",
        confirm: "确认",
        memberSource: "来源",
        memberJoined: "加入时间",
        archiveEffect: "归档后该 Team 从工作区选择器中移除，成员派生可见性撤销。",
        suspendEffect: "暂停后该 Team 不可用，恢复前成员无法进入该工作区。",
        activateEffect: "启用后该 Team 重新可被选择，成员恢复进入该工作区。",
        reservedHint: "保留工作区，状态不可变更。",
        createTeam: "申请新 Team",
        createTeamHint: "新 Team 通过 Team 申请创建，提交后由审核者批准。",
    },
    en: {
        title: "Teams",
        description: "Review the visible Team directory and operational status.",
        refresh: "Refresh",
        team: "Team",
        status: "Status",
        owner: "Owner",
        members: "Members",
        created: "Created",
        updated: "Updated",
        descriptionLabel: "Description",
        actions: "Actions",
        suspend: "Suspend",
        activate: "Activate",
        archive: "Archive",
        details: "Details",
        detailsTitle: "Team details",
        edit: "Edit",
        editTitle: "Edit Team",
        namePlaceholder: "Team name",
        descriptionPlaceholder: "Team description",
        save: "Save",
        saved: "Team updated.",
        close: "Close",
        invalidDetails: "The Team details response is invalid.",
        memberSource: "Source",
        memberJoined: "Joined",
        active: "Active",
        suspended: "Suspended",
        archived: "Archived",
        permission: "You do not have permission to view Teams.",
        invalid: "The Teams response is invalid.",
        confirmStatusTitle: "Confirm Team status change",
        confirmStatusBody: "This takes effect immediately:",
        statusTeam: "Team",
        statusAction: "Action",
        statusOwner: "Owner",
        statusMembers: "Members",
        confirm: "Confirm",
        archiveEffect: "After archiving, this Team leaves the workspace selector and derived member visibility is revoked.",
        suspendEffect: "After suspending, this Team is unavailable and members cannot enter the workspace until restored.",
        activateEffect: "After activating, this Team can be selected again and members regain access to the workspace.",
        reservedHint: "Reserved workspace; its status cannot change.",
        createTeam: "Apply for a Team",
        createTeamHint: "New Teams are created through Team applications and approved by reviewers.",
    },
} as const;

const {locale, t: baseT} = useLocale();
const router = useRouter();
const authorization = useAuthorization();
const workspaceContext = useWorkspaceContext();
const request = useRequest<Team[]>();
const toast = useToast();
const permissionFailure = ref<RequestFailure | null>(null);
const mutationPending = ref(false);
const detailRequest = useRequest<Team>();
const memberRequest = useRequest<TeamMember[]>();
const detailOpen = ref(false);
const selectedTeam = ref<Team | null>(null);
const editOpen = ref(false);
const editTeam = ref<Team | null>(null);
const editName = ref("");
const editDescription = ref("");
type TeamStatusAction = "suspend" | "activate" | "archive";
const statusConfirmation = ref<{team: Team; status: TeamStatusAction} | null>(null);
const localeName = computed(() => locale.value === "zh" ? "zh-CN" : "en-US");
const phase = computed<RequestPhase>(() => {
    if (permissionFailure.value) return "forbidden";
    if (authorization.phase.value !== "success") return authorization.phase.value;
    if (!authorization.can("system.teams.read")) return "forbidden";
    return request.phase.value;
});
// 全局门按"被调用的节点"判定授权（presentation/authorization.py），所以每个按钮按自己
// 动作的节点开门：只持 system.teams.manage 的账户不该看到会 403 的暂停/归档按钮。
const canEditTeam = computed(() => authorization.can("system.teams.manage"));
const canReadTeam = computed(() => authorization.can("system.teams.read_team"));
const canConfirmTeam = computed(() => authorization.can("system.teams.confirm"));
const canSuspendTeam = computed(
    () => canConfirmTeam.value && authorization.can("system.teams.suspend"),
);
const canActivateTeam = computed(
    () => canConfirmTeam.value && authorization.can("system.teams.activate"),
);
const canArchiveTeam = computed(
    () => canConfirmTeam.value && authorization.can("system.teams.archive"),
);
const canCreateApplication = computed(
    () => authorization.can("system.team_applications.create"),
);

// 保留工作区（System / Template Team）没有恢复接口，状态变更在后端被拒绝。
function isReservedTeam(team: Team) {
    return team.team_id === "system" || team.team_id === "template";
}

function openApplication() {
    void router.push({name: "applications"});
}

function pageT(key: string) {
    const value = key.split(".").reduce<unknown>(
        (current, part) => current && typeof current === "object"
            ? (current as Record<string, unknown>)[part]
            : undefined,
        messages[locale.value],
    );
    return String(value ?? key);
}

function parseTeams(response: TransportResponse): Team[] {
    const failure = responseFailure(response);
    if (failure) throw failure;
    const values = response.data?.teams;
    if (!Array.isArray(values)) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    return values.filter((value): value is Team =>
        Boolean(value)
        && typeof value === "object"
        && typeof (value as Record<string, unknown>).team_id === "string"
        && typeof (value as Record<string, unknown>).name === "string",
    );
}

async function load() {
    permissionFailure.value = null;
    try {
        await authorization.ensure(null);
        if (!authorization.can("system.teams.read")) {
            permissionFailure.value = new RequestFailure(pageT("permission"), "forbidden", "permission_denied");
            return;
        }
        await request.run(async (signal) => {
            const response = await transport.request({
                action: "read",
                node: "system.teams",
            });
            if (signal.aborted) {
                throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
            }
            return parseTeams(response);
        }, {isEmpty: (items) => items.length === 0});
    } catch (cause) {
        if (cause instanceof RequestFailure && cause.code === "permission_denied") {
            permissionFailure.value = cause;
        }
    }
}

function statusLabel(status?: string) {
    if (status === "suspended") return pageT("suspended");
    if (status === "archived") return pageT("archived");
    return pageT("active");
}

function changeStatus(team: Team, status: TeamStatusAction) {
    if (isReservedTeam(team) || !canConfirmTeam.value || mutationPending.value) return;
    // 先展示确认弹窗（目标、影响、后果），用户确认后再走确认令牌与变更。
    statusConfirmation.value = {team, status};
}

function closeStatusConfirmation() {
    if (!mutationPending.value) statusConfirmation.value = null;
}

async function confirmStatusChange() {
    const pending = statusConfirmation.value;
    if (!pending || mutationPending.value) return;
    mutationPending.value = true;
    try {
        const confirmation = await transport.request({
            action: "confirm",
            node: "system.teams",
            data: {team_id: pending.team.team_id},
        });
        const confirmationFailure = responseFailure(confirmation);
        if (confirmationFailure) throw confirmationFailure;
        const confirmationToken = confirmation.data?.confirmation_token;
        if (typeof confirmationToken !== "string" || !confirmationToken) {
            throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
        }
        const response = await transport.request({
            action: pending.status,
            node: "system.teams",
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {team_id: pending.team.team_id, confirmation_token: confirmationToken},
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        toast.push(`${pending.team.name}: ${pageT(pending.status)}`, "success");
        statusConfirmation.value = null;
        await load();
        await workspaceContext.loadOptions({preserveSelection: true});
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
        mutationPending.value = false;
    }
}

async function showDetails(team: Team) {
    selectedTeam.value = team;
    detailOpen.value = true;
    try {
        await detailRequest.run(async (signal) => {
            const response = await transport.request({
                action: "read_team",
                node: "system.teams",
                data: {team_id: team.team_id},
            });
            if (signal.aborted) {
                throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
            }
            const failure = responseFailure(response);
            if (failure) throw failure;
            if (!response.data || typeof response.data.team_id !== "string") {
                throw new RequestFailure(pageT("invalidDetails"), "validation", "invalid_response");
            }
            return response.data as unknown as Team;
        });
        await memberRequest.run(async (signal) => {
            const response = await transport.request({
                action: "read",
                node: `team.${team.team_id}.users`,
                data: {limit: 100, offset: 0},
            });
            if (signal.aborted) {
                throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
            }
            const failure = responseFailure(response);
            if (failure) throw failure;
            const values = response.data?.members;
            if (!Array.isArray(values)) {
                throw new RequestFailure(pageT("invalidDetails"), "validation", "invalid_response");
            }
            return values.filter((value): value is TeamMember => Boolean(value)
                && typeof value === "object"
                && typeof (value as Record<string, unknown>).github_user_id === "string");
        }, {isEmpty: (items) => items.length === 0});
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    }
}

function closeDetails() {
    detailOpen.value = false;
    selectedTeam.value = null;
    detailRequest.reset();
    memberRequest.reset();
}

function openEdit(team: Team) {
    if (!canEditTeam.value || mutationPending.value) return;
    editTeam.value = team;
    editName.value = team.name;
    editDescription.value = team.description ?? "";
    editOpen.value = true;
}

function closeEdit() {
    if (mutationPending.value) return;
    editOpen.value = false;
    editTeam.value = null;
}

async function saveEdit() {
    const team = editTeam.value;
    const name = editName.value.trim();
    if (!team || !canEditTeam.value || mutationPending.value || !name) return;
    mutationPending.value = true;
    try {
        const response = await transport.request({
            action: "manage",
            node: "system.teams",
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {
                team_id: team.team_id,
                name,
                description: editDescription.value.trim(),
            },
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        editOpen.value = false;
        editTeam.value = null;
        toast.push(`${team.name}: ${pageT("saved")}`, "success");
        await load();
        // 编辑可能改了当前工作区名称，刷新工作区选项保持标题一致。
        await workspaceContext.loadOptions({preserveSelection: true});
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
        mutationPending.value = false;
    }
}

onMounted(load);
</script>

<template>
    <div class="teams-page">
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
                    v-if="canCreateApplication"
                    variant="primary"
                    :disabled="mutationPending"
                    :title="pageT('createTeamHint')"
                    @click="openApplication"
                >
                    {{ pageT("createTeam") }}
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
                    {key: 'name', label: pageT('team')},
                    {key: 'status', label: pageT('status')},
                    {key: 'owner_user_id', label: pageT('owner')},
                    {key: 'member_count', label: pageT('members')},
                    {key: 'created_at', label: pageT('created')},
                    {key: 'team_id', label: pageT('actions')},
                ]"
                :rows="request.data.value"
            >
                <template #name="{row}">
                    <strong>{{ row.name }}</strong>
                    <span class="secondary-line">{{ row.team_id }}</span>
                </template>
                <template #status="{row}">
                    <span class="status" :data-status="row.status">
                        {{ statusLabel(row.status) }}
                    </span>
                </template>
                <template #owner_user_id="{row}">
                    {{ row.owner_user_id || baseT("common.emptyValue") }}
                </template>
                <template #member_count="{row}">
                    {{ row.member_count ?? 0 }}
                </template>
                <template #created_at="{row}">
                    {{ formatTimestamp(row.created_at, localeName) }}
                </template>
                <template #team_id="{row}">
                    <div class="row-actions">
                        <UiButton v-if="canReadTeam" variant="ghost" @click="showDetails(row)">
                            {{ pageT("details") }}
                        </UiButton>
                        <UiButton v-if="canEditTeam" variant="ghost" :disabled="mutationPending" @click="openEdit(row)">
                            {{ pageT("edit") }}
                        </UiButton>
                        <UiButton
                            v-if="canSuspendTeam && row.status === 'active'"
                            variant="ghost"
                            :disabled="mutationPending || isReservedTeam(row)"
                            :title="isReservedTeam(row) ? pageT('reservedHint') : undefined"
                            @click="changeStatus(row, 'suspend')"
                        >
                            {{ pageT("suspend") }}
                        </UiButton>
                        <UiButton
                            v-if="canActivateTeam && row.status === 'suspended'"
                            variant="ghost"
                            :disabled="mutationPending || isReservedTeam(row)"
                            :title="isReservedTeam(row) ? pageT('reservedHint') : undefined"
                            @click="changeStatus(row, 'activate')"
                        >
                            {{ pageT("activate") }}
                        </UiButton>
                        <UiButton
                            v-if="canArchiveTeam && row.status !== 'archived'"
                            variant="ghost"
                            :disabled="mutationPending || isReservedTeam(row)"
                            :title="isReservedTeam(row) ? pageT('reservedHint') : undefined"
                            @click="changeStatus(row, 'archive')"
                        >
                            {{ pageT("archive") }}
                        </UiButton>
                    </div>
                </template>
            </UiDataTable>
        </section>
        <UiDialog :open="detailOpen" :title="pageT('detailsTitle')" @close="closeDetails">
            <RequestState
                :phase="detailRequest.phase.value"
                @retry="selectedTeam && showDetails(selectedTeam)"
                @cancel="detailRequest.cancel"
            />
            <dl v-if="detailRequest.data.value" class="details-list">
                <div><dt>{{ pageT("team") }}</dt><dd>{{ detailRequest.data.value.name }}</dd></div>
                <div><dt>{{ baseT("common.id") }}</dt><dd>{{ detailRequest.data.value.team_id }}</dd></div>
                <div><dt>{{ pageT("owner") }}</dt><dd>{{ detailRequest.data.value.owner_user_id || baseT("common.emptyValue") }}</dd></div>
                <div><dt>{{ pageT("status") }}</dt><dd>{{ statusLabel(detailRequest.data.value.status) }}</dd></div>
                <div><dt>{{ pageT("descriptionLabel") }}</dt><dd>{{ detailRequest.data.value.description || baseT("common.emptyValue") }}</dd></div>
                <div><dt>{{ pageT("members") }}</dt><dd>{{ detailRequest.data.value.member_count ?? 0 }}</dd></div>
                <div><dt>{{ pageT("created") }}</dt><dd>{{ formatTimestamp(detailRequest.data.value.created_at, localeName) }}</dd></div>
                <div><dt>{{ pageT("updated") }}</dt><dd>{{ formatTimestamp(detailRequest.data.value.updated_at, localeName) }}</dd></div>
            </dl>
            <section v-if="memberRequest.data.value" class="member-section">
                <h3>{{ pageT("members") }}</h3>
                <UiDataTable
                    :columns="[
                        {key: 'github_user_id', label: pageT('owner')},
                        {key: 'status', label: pageT('status')},
                        {key: 'source_id', label: pageT('memberSource')},
                        {key: 'joined_at', label: pageT('memberJoined')},
                    ]"
                    :rows="memberRequest.data.value"
                >
                    <template #github_user_id="{row}">
                        <strong>{{ row.login_snapshot || row.github_user_id }}</strong>
                        <span class="secondary-line">{{ row.github_user_id }}</span>
                    </template>
                    <template #status="{row}">
                        {{ statusLabel(row.status) }}
                    </template>
                    <template #source_id="{row}">
                        {{ row.source_type || baseT("common.emptyValue") }} / {{ row.source_id || baseT("common.emptyValue") }}
                    </template>
                    <template #joined_at="{row}">
                        {{ formatTimestamp(row.joined_at, localeName) }}
                    </template>
                </UiDataTable>
            </section>
            <template #footer>
                <UiButton variant="ghost" @click="closeDetails">{{ pageT("close") }}</UiButton>
            </template>
        </UiDialog>
        <UiDialog :open="editOpen" :title="pageT('editTitle')" @close="closeEdit">
            <div class="edit-form">
                <UiInput
                    v-model="editName"
                    :aria-label="pageT('namePlaceholder')"
                    :placeholder="pageT('namePlaceholder')"
                />
                <textarea
                    v-model="editDescription"
                    :aria-label="pageT('descriptionPlaceholder')"
                    :placeholder="pageT('descriptionPlaceholder')"
                ></textarea>
            </div>
            <template #footer>
                <UiButton variant="ghost" :disabled="mutationPending" @click="closeEdit">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton
                    variant="primary"
                    :disabled="mutationPending || !editName.trim()"
                    @click="saveEdit"
                >
                    {{ pageT("save") }}
                </UiButton>
            </template>
        </UiDialog>
        <UiDialog
            :open="Boolean(statusConfirmation)"
            :title="pageT('confirmStatusTitle')"
            @close="closeStatusConfirmation"
        >
            <div v-if="statusConfirmation" class="confirm-form">
                <p>{{ pageT("confirmStatusBody") }}</p>
                <dl class="details-list">
                    <div><dt>{{ pageT("statusTeam") }}</dt><dd>{{ statusConfirmation.team.name }} ({{ statusConfirmation.team.team_id }})</dd></div>
                    <div>
                        <dt>{{ pageT("statusAction") }}</dt>
                        <dd>{{ pageT(statusConfirmation.status) }}</dd>
                    </div>
                    <div><dt>{{ pageT("statusOwner") }}</dt><dd>{{ statusConfirmation.team.owner_user_id || baseT("common.emptyValue") }}</dd></div>
                    <div><dt>{{ pageT("statusMembers") }}</dt><dd>{{ statusConfirmation.team.member_count ?? 0 }}</dd></div>
                </dl>
                <p class="confirm-effect">
                    {{ statusConfirmation.status === "archive"
                        ? pageT("archiveEffect")
                        : statusConfirmation.status === "activate"
                            ? pageT("activateEffect")
                            : pageT("suspendEffect") }}
                </p>
            </div>
            <template #footer>
                <UiButton variant="ghost" :disabled="mutationPending" @click="closeStatusConfirmation">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton
                    variant="primary"
                    :disabled="mutationPending"
                    @click="confirmStatusChange"
                >
                    {{ pageT("confirm") }}
                </UiButton>
            </template>
        </UiDialog>
    </div>
</template>

<style scoped>
.teams-page {
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

.status[data-status="active"] {
    color: #78d88c;
}

.status[data-status="suspended"],
.status[data-status="archived"] {
    color: #ffcb73;
}

.row-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
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

.member-section {
    display: grid;
    gap: 12px;
    margin-top: 20px;
}

.member-section h3 {
    margin: 0;
    font-size: 14px;
}

.details-list dd {
    margin: 0;
    text-align: right;
}

.edit-form {
    display: grid;
    gap: 12px;
}

.confirm-form {
    display: grid;
    gap: 12px;
}

.confirm-form > p {
    margin: 0;
    color: #9aa6b2;
}

.confirm-effect {
    padding: 10px 12px;
    border: 1px solid rgba(255, 155, 155, 0.35);
    border-radius: 8px;
    background: rgba(255, 155, 155, 0.08);
    color: #ffb3b3 !important;
}

.edit-form textarea {
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
