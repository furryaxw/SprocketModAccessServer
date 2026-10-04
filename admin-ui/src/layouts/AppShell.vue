<script lang="ts" setup>
import {computed, nextTick, onBeforeUnmount, onMounted, ref, watch} from "vue";
import {RouterLink, RouterView, useRoute, useRouter} from "vue-router";
import ToastHost from "../components/ToastHost.vue";
import LanguagePicker from "../components/LanguagePicker.vue";
import {useToast} from "../composables/useToast";
import {useLocale} from "../i18n";
import {useSession} from "../composables/useSession";
import {
    useWorkspaceContext,
    type WorkspaceOption,
} from "../composables/useWorkspaceContext";
import {logger} from "../api/logger";
import {
    transport,
    type TransportConnectionState,
    type TransportResponse,
} from "../api/transport";
import {useAuthorization} from "../composables/useAuthorization";
import {responseFailure} from "../composables/requestState";
import UiButton from "../components/UiButton.vue";
import UiDialog from "../components/UiDialog.vue";
import UiInput from "../components/UiInput.vue";

const {t} = useLocale();
const toast = useToast();
const route = useRoute();
const router = useRouter();
const {authenticated, user: sessionUser, signOut} = useSession();
const authorization = useAuthorization();
const {
    options: workspaceOptions,
    selected: selectedWorkspace,
    selectWorkspace,
    loadOptions,
} = useWorkspaceContext();
const collapsed = ref(false);
const workspaceMenuOpen = ref(false);
const mobileNavOpen = ref(false);
const userMenuOpen = ref(false);
const workspaceMenuElement = ref<HTMLElement | null>(null);
const userMenuElement = ref<HTMLElement | null>(null);
const workspaces = workspaceOptions;
const workspaceReady = ref(false);
let workspaceLoadStarted = false;
let workspaceReloadQueued = false;
let stopWorkspaceSubscription: (() => void) | null = null;
let stopTransportStatusSubscription: (() => void) | null = null;
const transportStatus = ref<TransportConnectionState>("idle");
const pageTitle = computed(() => {
    const titleByRoute: Record<string, string> = {
        overview: t("shell.overview"),
        "overview-content": t("shell.overview"),
        users: t("shell.users"),
        teams: t("shell.teams"),
        "permission-templates": t("shell.permissionTemplates"),
        "permission-assignments": t("shell.permissionAssignments"),
        packages: t("shell.packages"),
        keys: t("shell.keys"),
        operations: t("shell.operations"),
        applications: t("shell.applications"),
        audit: t("shell.audit"),
    };
    return titleByRoute[String(route.name ?? "")] ?? String(route.name ?? "");
});
const workspaceLabel = computed(() => selectedWorkspace.value?.name ?? "");
const userLabel = computed(() =>
    String(
        sessionUser.value?.display_name
        ?? sessionUser.value?.login_snapshot
        ?? sessionUser.value?.github_user_id
        ?? t("common.user"),
    ),
);
const userAvatarUrl = computed(() => {
    const userId = String(sessionUser.value?.github_user_id ?? "").trim();
    return userId ? `https://avatars.githubusercontent.com/u/${userId}?v=4` : "";
});
const selectedWorkspaceValue = computed(() =>
    selectedWorkspace.value ? workspaceValue(selectedWorkspace.value) : "",
);
const navigation = computed(() => {
    const workspace = selectedWorkspace.value;
    if (!workspace || !authorization.ready.value) return [];

    const systemWorkspace = workspace.workspace_kind === "system";
    const teamPrefix = `team.${workspace.team_id}`;
    const items = [
        {
            name: "overview-content",
            path: "/overview",
            label: t("shell.overview"),
            visible: authorization.can(
                systemWorkspace
                    ? "system.overview.read"
                    : `${teamPrefix}.overview.read`,
            ),
        },
        {
            name: "users",
            path: "/overview/users",
            label: t("shell.users"),
            visible: authorization.can(
                systemWorkspace ? "system.users.read" : `${teamPrefix}.users.read`,
            ),
        },
        {
            name: "teams",
            path: "/overview/teams",
            label: t("shell.teams"),
            visible: systemWorkspace && authorization.can("system.teams.read"),
        },
        {
            name: "permission-templates",
            path: "/overview/permission-templates",
            label: t("shell.permissionTemplates"),
            visible: authorization.can(`${teamPrefix}.permission_templates.read`),
        },
        {
            name: "permission-assignments",
            path: "/overview/permission-assignments",
            label: t("shell.permissionAssignments"),
            visible: authorization.can(`${teamPrefix}.permission_assignments.read`),
        },
        {
            name: "packages",
            path: "/overview/packages",
            label: t("shell.packages"),
            visible: authorization.can(
                systemWorkspace
                    ? "team.system.packages.read"
                    : `${teamPrefix}.packages.read`,
            ),
        },
        {
            name: "keys",
            path: "/overview/keys",
            label: t("shell.keys"),
            visible: authorization.can(`${teamPrefix}.keys.read`),
        },
        {
            name: "operations",
            path: "/overview/operations",
            label: t("shell.operations"),
            visible: systemWorkspace
                && authorization.can("system.operations.status.read"),
        },
        {
            name: "applications",
            path: "/overview/applications",
            label: t("shell.applications"),
            visible: systemWorkspace && (
                authorization.can("system.team_applications.read")
                || authorization.can("system.team_applications.create")
            ),
        },
        {
            name: "audit",
            path: "/overview/audit",
            label: t("shell.audit"),
            visible: authorization.can(`${teamPrefix}.audit.read`),
        },
    ];
    return items.filter((item) => item.visible);
});

function workspaceValue(workspace: WorkspaceOption) {
    return workspace.workspace_kind === "system"
        ? `system:${workspace.team_id}`
        : `team:${workspace.team_id}`;
}

function workspaceKey(workspace: WorkspaceOption) {
    return workspaceValue(workspace);
}

function findWorkspace(value: string) {
    return workspaces.value.find((item) => workspaceValue(item) === value);
}

function handleWorkspaceBroadcast(message: TransportResponse) {
    if (message.request_id) return;
    if (message.kind !== "resource.changed") return;
    const node = String(message.node ?? "");
    const isTeamLifecycle = /^team\.[^.]+$/.test(node);
    const isTeamDirectoryChange = node === "system.teams"
        || node === "system.team_applications";
    if (!isTeamLifecycle && !isTeamDirectoryChange) return;
    if (workspaceLoadStarted) {
        workspaceReloadQueued = true;
        return;
    }
    void loadTeams({preserveSelection: true});
}

async function loadTeams(options: {preserveSelection?: boolean} = {}) {
    if (!authenticated.value || workspaceLoadStarted) return;
    workspaceLoadStarted = true;
    logger.info("workspace.options.load.start");
    try {
        const loaded = await loadOptions(options);
        logger.info("workspace.options.loaded", {count: loaded.length});
    } catch (cause) {
        logger.error("workspace.options.error", {
            message: cause instanceof Error ? cause.message : String(cause),
        });
        toast.push(t("common.workspaceLoadFailed"), "error");
    } finally {
        workspaceLoadStarted = false;
        if (authenticated.value) {
            workspaceReady.value = true;
        }
        if (workspaceReloadQueued) {
            workspaceReloadQueued = false;
            void loadTeams({preserveSelection: true});
        }
    }
}

async function chooseWorkspace(option: WorkspaceOption): Promise<boolean> {
    if (await selectWorkspace(option)) {
        workspaceMenuOpen.value = false;
        toast.push(workspaceLabel.value, "info");
        return true;
    } else {
        logger.warn("workspace.switch.failed", {
            kind: option.workspace_kind,
            teamId: option.team_id,
        });
        toast.push(t("common.workspaceSwitchFailed"), "error");
        return false;
    }
}

async function chooseWorkspaceValue(value: string, event: Event) {
    const workspace = findWorkspace(value);
    if (!workspace) {
        logger.warn("workspace.switch.unknown", {value});
        restoreWorkspaceSelect(event);
        return;
    }
    if (!await chooseWorkspace(workspace)) {
        restoreWorkspaceSelect(event);
    }
}

function restoreWorkspaceSelect(event: Event) {
    const target = event.target;
    if (!(target instanceof HTMLSelectElement)) return;
    target.value = selectedWorkspaceValue.value;
    void nextTick(() => {
        target.value = selectedWorkspaceValue.value;
    });
}

function closeWorkspaceMenuOnOutsideClick(event: MouseEvent) {
    if (!(event.target instanceof Node)) return;
    if (workspaceMenuElement.value && !workspaceMenuElement.value.contains(event.target)) {
        workspaceMenuOpen.value = false;
    }
}

function toggleUserMenu() {
    userMenuOpen.value = !userMenuOpen.value;
}

function closeUserMenu() {
    userMenuOpen.value = false;
}

function closeUserMenuOnOutsideClick(event: MouseEvent) {
    if (!(event.target instanceof Node)) return;
    if (userMenuElement.value && !userMenuElement.value.contains(event.target)) {
        closeUserMenu();
    }
}

function handleUserMenuKeydown(event: KeyboardEvent) {
    if (event.key !== "Escape") return;
    event.preventDefault();
    closeUserMenu();
    userMenuElement.value?.querySelector<HTMLButtonElement>(".user-button")?.focus();
}

function handleSignOut() {
    closeUserMenu();
    signOut();
    void router.replace({name: "login"});
}

// 接受 Team 邀请：账户菜单入口，令牌即凭据，接受后刷新工作区列表。
const invitationOpen = ref(false);
const invitationToken = ref("");
const invitationPending = ref(false);

function openInvitation() {
    closeUserMenu();
    invitationOpen.value = true;
    invitationToken.value = "";
}

function closeInvitation() {
    if (invitationPending.value) return;
    invitationOpen.value = false;
    invitationToken.value = "";
}

async function acceptInvitation() {
    const token = invitationToken.value.trim();
    if (!token || invitationPending.value) return;
    invitationPending.value = true;
    try {
        const response = await transport.request({
            action: "accept",
            node: "system.team_invitations",
            data: {token},
        });
        const failure = responseFailure(response);
        if (failure) throw failure;
        invitationOpen.value = false;
        invitationToken.value = "";
        toast.push(t("invitation.accepted"), "success");
        await loadTeams();
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
        invitationPending.value = false;
    }
}

const transportStatusLabel = computed(() => {
    const labels: Record<TransportConnectionState, string> = {
        idle: t("common.websocketStatusDisconnected"),
        connecting: t("common.websocketStatusConnecting"),
        connected: t("common.websocketStatusConnected"),
        disconnected: t("common.websocketStatusDisconnected"),
    };
    return labels[transportStatus.value];
});

async function reconnect() {
    if (transportStatus.value === "connecting") return;
    logger.info("ws.reconnect.requested");
    workspaceReady.value = false;
    try {
        await transport.connect();
        await authorization.refresh(null);
        await loadTeams({preserveSelection: true});
        workspaceReady.value = true;
        toast.push(transportStatusLabel.value, "info");
    } catch (cause) {
        logger.warn("ws.reconnect.failed", {
            message: cause instanceof Error ? cause.message : String(cause),
        });
        toast.push(t("common.websocketConnectionFailed"), "error");
    }
}

watch(
    authenticated,
    (isAuthenticated) => {
        if (isAuthenticated) {
            void loadTeams();
        }
    },
    {immediate: true},
);
// 这个 watch 的依赖会被它自己触发的导航改写：`navigation` 每次求值都产生新数组，授权快照刷新
// 又会让 ready 短暂为假。没有闸门时"重定向 → 守卫刷新快照 → 依赖再变 → 再重定向"会自持成导航
// 循环（每轮一次 session.refresh 加一次 loadTeams）。同一个"工作区 + 路由"只重定向一次；
// 只有路由在当前工作区可见（或已经回到概览）才清记录，快照未就绪时不清——否则 ready 的闪烁
// 会把记录洗掉，每轮又能重定向一次。
let redirectedFor = "";
watch(
    [selectedWorkspace, navigation, () => route.name],
    ([workspace, items, routeName]) => {
        if (!workspace || routeName === "overview" || routeName === "overview-content") {
            redirectedFor = "";
            return;
        }
        if (!authorization.ready.value) return;
        if (items.some((item) => item.name === routeName)) {
            redirectedFor = "";
            return;
        }
        const key = `${workspaceValue(workspace)}|${String(routeName ?? "")}`;
        if (redirectedFor === key) return;
        redirectedFor = key;
        void router.replace({name: "overview-content"});
    },
    {immediate: true},
);
onMounted(() => logger.debug("shell.mounted"));
onMounted(() => {
    stopWorkspaceSubscription = transport.subscribe(handleWorkspaceBroadcast);
    stopTransportStatusSubscription = transport.subscribeStatus((status) => {
        transportStatus.value = status;
    });
    document.addEventListener("click", closeUserMenuOnOutsideClick);
    document.addEventListener("click", closeWorkspaceMenuOnOutsideClick);
});
onBeforeUnmount(() => {
    stopWorkspaceSubscription?.();
    stopWorkspaceSubscription = null;
    stopTransportStatusSubscription?.();
    stopTransportStatusSubscription = null;
    document.removeEventListener("click", closeUserMenuOnOutsideClick);
    document.removeEventListener("click", closeWorkspaceMenuOnOutsideClick);
});

function toggleNavigation() {
    logger.debug("navigation.toggle", {mobile: window.innerWidth <= 760});
    if (window.innerWidth <= 760) {
        mobileNavOpen.value = !mobileNavOpen.value;
        return;
    }
    collapsed.value = !collapsed.value;
    logger.info("navigation.collapsed", {collapsed: collapsed.value});
}
</script>

<template>
    <div
        :class="{ 'is-collapsed': collapsed, 'mobile-open': mobileNavOpen }"
        class="shell"
    >
        <button
            :title="t('shell.collapse')"
            class="zone zone-one desktop-zone"
            @click="toggleNavigation"
        >
            <span aria-hidden="true" class="logo-mark">⚡</span>
            <strong v-if="!collapsed" class="logo-name">{{ t("app.name") }}</strong>
        </button>

        <header class="zone zone-two">
            <div class="header-leading">
                <button
                    :title="t('shell.expand')"
                    class="mobile-expand"
                    @click="toggleNavigation"
                >
                    {{ mobileNavOpen ? "×" : "☰" }}
                </button>
                <div class="header-title">
                    <span class="eyebrow">{{ workspaceLabel }}</span>
                    <h1>{{ pageTitle }}</h1>
                </div>
            </div>
            <div class="header-actions">
                <button
                    v-if="authenticated && transportStatus !== 'connected'"
                    :class="`transport-status is-${transportStatus}`"
                    :disabled="transportStatus === 'connecting'"
                    type="button"
                    @click="reconnect"
                >
                    <span aria-hidden="true" class="status-dot"></span>
                    <span>{{ transportStatusLabel }}</span>
                    <span v-if="transportStatus === 'disconnected'">
                        {{ t("common.reconnect") }}
                    </span>
                </button>
                <LanguagePicker />
                <div ref="userMenuElement" class="user-menu" @click.stop @keydown="handleUserMenuKeydown">
                    <button
                        :aria-expanded="userMenuOpen"
                        :aria-label="userLabel"
                        :title="userLabel"
                        class="user-button"
                        type="button"
                        @click="toggleUserMenu"
                    >
                        <img
                            v-if="userAvatarUrl"
                            :alt="userLabel"
                            :src="userAvatarUrl"
                        />
                        <span v-else aria-hidden="true">{{ userLabel.slice(0, 1).toUpperCase() }}</span>
                    </button>
                    <div v-if="userMenuOpen" class="user-menu-panel">
                        <div class="user-menu-head">
                            <strong>{{ userLabel }}</strong>
                            <span>{{ sessionUser?.github_user_id }}</span>
                        </div>
                    <button type="button" @click="openInvitation">{{ t("invitation.menu") }}</button>
                    <button type="button" @click="handleSignOut">{{ t("common.signOut") }}</button>
                    </div>
                </div>
            </div>
        </header>

        <div
            v-if="mobileNavOpen"
            class="mobile-overlay"
            @click="mobileNavOpen = false"
        ></div>
        <aside v-if="mobileNavOpen" class="mobile-drawer">
            <button
                :title="t('shell.collapse')"
                class="zone zone-one mobile-zone"
                @click="toggleNavigation"
            >
                <span aria-hidden="true" class="logo-mark">⚡</span>
                <strong class="logo-name">{{ t("app.name") }}</strong>
                <span aria-hidden="true" class="collapse-glyph">←</span>
            </button>
            <section class="zone zone-three mobile-zone">
                <span class="eyebrow">{{ t("shell.workspace") }}</span>
                <select
                    :value="selectedWorkspaceValue"
                    class="workspace-select"
                    @change="chooseWorkspaceValue(($event.target as HTMLSelectElement).value, $event)"
                >
                    <option v-if="!selectedWorkspace" disabled value=""></option>
                    <option
                        v-for="workspace in workspaces"
                        :key="workspaceKey(workspace)"
                        :value="workspaceValue(workspace)"
                    >
                        {{ workspace.name }}
                    </option>
                </select>
                <nav class="page-nav" :aria-label="t('common.primaryNavigation')">
                    <RouterLink
                        v-for="item in navigation"
                        :key="item.name"
                        :to="item.path"
                        @click="mobileNavOpen = false"
                    >
                        {{ item.label }}
                    </RouterLink>
                </nav>
            </section>
        </aside>

        <aside
            :class="{ 'mobile-open': mobileNavOpen }"
            class="zone zone-three desktop-zone"
        >
            <span class="eyebrow">{{ t("shell.workspace") }}</span>
            <div v-if="collapsed" ref="workspaceMenuElement" class="workspace-popover">
                <button
                    type="button"
                    class="workspace-trigger"
                    @click="workspaceMenuOpen = !workspaceMenuOpen"
                >
                    {{ workspaceLabel.slice(0, 1).toUpperCase() }}
                </button>
                <div v-if="workspaceMenuOpen" class="workspace-menu">
                    <button
                        v-for="workspace in workspaces"
                        :key="workspaceKey(workspace)"
                        type="button"
                        @click="chooseWorkspace(workspace)"
                    >
                        {{ workspace.name }}
                    </button>
                </div>
            </div>
            <select
                v-else
                :value="selectedWorkspaceValue"
                class="workspace-select"
                @change="chooseWorkspaceValue(($event.target as HTMLSelectElement).value, $event)"
            >
                <option v-if="!selectedWorkspace" disabled value=""></option>
                <option
                    v-for="workspace in workspaces"
                    :key="workspaceKey(workspace)"
                    :value="workspaceValue(workspace)"
                >
                    {{ workspace.name }}
                </option>
            </select>
            <nav class="page-nav" :aria-label="t('common.primaryNavigation')">
                <RouterLink
                    v-for="item in navigation"
                    :key="item.name"
                    :to="item.path"
                    @click="mobileNavOpen = false"
                >
                    {{ item.label }}
                </RouterLink>
            </nav>
        </aside>

        <section class="zone zone-four">
            <main class="main-content">
                <RouterView v-if="workspaceReady"/>
            </main>
        </section>
        <ToastHost :items="toast.items"/>
        <UiDialog :open="invitationOpen" :title="t('invitation.title')" @close="closeInvitation">
            <div class="invitation-form">
                <p>{{ t("invitation.hint") }}</p>
                <UiInput
                    v-model="invitationToken"
                    :aria-label="t('invitation.token')"
                    :placeholder="t('invitation.token')"
                />
            </div>
            <template #footer>
                <UiButton variant="ghost" :disabled="invitationPending" @click="closeInvitation">
                    {{ t("common.close") }}
                </UiButton>
                <UiButton
                    variant="primary"
                    :disabled="invitationPending || !invitationToken.trim()"
                    @click="acceptInvitation"
                >
                    {{ t("invitation.accept") }}
                </UiButton>
            </template>
        </UiDialog>
    </div>
</template>

<style scoped>
.shell {
    min-height: 100vh;
    height: 100vh;
    display: grid;
    grid-template-columns: 248px minmax(0, 1fr);
    grid-template-rows: 72px minmax(0, 1fr);
    grid-template-areas: "one two" "three four";
    overflow: hidden;
    background: #101216;
}

.shell.is-collapsed {
    grid-template-columns: 72px minmax(0, 1fr);
}

.zone {
    min-width: 0;
    min-height: 0;
    border-color: rgba(255, 255, 255, 0.09);
    border-style: solid;
    background: #171a21;
}

.zone-one {
    grid-area: one;
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 0 16px;
    border-width: 0 1px 1px 0;
    color: #edf1f7;
    cursor: pointer;
    text-align: left;
}

.zone-one:hover {
    background: #1d222c;
}

.collapse-glyph {
    margin-left: auto;
    color: #9aa6b2;
}

.logo-mark {
    display: grid;
    width: 34px;
    height: 34px;
    flex: 0 0 34px;
    place-items: center;
    border-radius: 10px;
    background: #5b8cff;
    color: #fff;
}

.logo-name {
    white-space: nowrap;
    font-size: 14px;
}

.zone-two {
    grid-area: two;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 22px;
    border-width: 0 0 1px;
}

.header-leading {
    display: flex;
    align-items: center;
    gap: 10px;
}

.header-title {
    display: grid;
    gap: 3px;
}

.header-title h1 {
    margin: 0;
    font-size: 18px;
}

.eyebrow {
    color: #9aa6b2;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}

.header-actions {
    display: flex;
    align-items: center;
    gap: 8px;
}

.header-actions button,
.mobile-expand {
    min-width: 34px;
    min-height: 34px;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    background: transparent;
    color: #edf1f7;
    cursor: pointer;
}

.transport-status {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 0 10px;
    white-space: nowrap;
}

.transport-status.is-connecting {
    color: #f2c14e;
}

.transport-status.is-disconnected,
.transport-status.is-idle {
    border-color: rgba(224, 108, 117, 0.45);
    color: #f0a4aa;
}

.transport-status:disabled {
    cursor: wait;
    opacity: 0.72;
}

.status-dot {
    width: 7px;
    height: 7px;
    flex: 0 0 7px;
    border-radius: 50%;
    background: currentColor;
}

.user-button {
    width: 36px;
    height: 36px;
    min-width: 36px !important;
    min-height: 36px !important;
    flex: 0 0 36px;
    display: grid;
    place-items: center;
    overflow: hidden;
    border-radius: 50% !important;
    background: rgba(91, 140, 255, 0.16) !important;
}

.user-button img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

.user-menu {
    position: relative;
}

.user-menu-panel {
    position: absolute;
    top: 42px;
    right: 0;
    z-index: 50;
    display: grid;
    min-width: 180px;
    padding: 8px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 10px;
    background: #1d212a;
    box-shadow: 0 14px 32px rgba(0, 0, 0, 0.32);
}

.user-menu-head {
    display: grid;
    gap: 3px;
    padding: 6px 8px 10px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.user-menu-head strong {
    font-size: 13px;
}

.user-menu-head span {
    color: #9aa6b2;
    font-size: 12px;
}

.user-menu-panel button {
    border: 0;
    border-radius: 7px;
    padding: 10px 8px;
    background: transparent;
    color: #edf1f7;
    text-align: left;
    cursor: pointer;
}

.user-menu-panel button:hover {
    background: rgba(91, 140, 255, 0.16);
}

.invitation-form {
    display: grid;
    gap: 12px;
}

.invitation-form p {
    margin: 0;
    color: #9aa6b2;
}

.mobile-expand {
    display: none;
}

.zone-three {
    grid-area: three;
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 18px 14px;
    border-width: 0 1px 0 0;
    overflow-y: auto;
    overflow-x: hidden;
}

.workspace-select,
.workspace-trigger {
    width: 100%;
    min-height: 38px;
    padding: 0 10px;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    background: #101216;
    color: #edf1f7;
}

.workspace-trigger {
    cursor: pointer;
}

.workspace-menu {
    position: fixed;
    top: 86px;
    left: 82px;
    z-index: 40;
    display: grid;
    width: 190px;
    padding: 6px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 10px;
    background: #1d212a;
    box-shadow: 0 14px 32px rgba(0, 0, 0, 0.32);
}

.workspace-popover {
    position: relative;
}

.workspace-menu button {
    border: 0;
    padding: 10px;
    border-radius: 7px;
    background: transparent;
    color: #edf1f7;
    text-align: left;
    cursor: pointer;
}

.workspace-menu button:hover {
    background: rgba(91, 140, 255, 0.16);
}

.shell.is-collapsed .zone-three > .eyebrow {
    display: none;
}

.shell.is-collapsed .page-nav a {
    width: 40px;
    overflow: hidden;
    white-space: nowrap;
    font-size: 0;
    text-align: center;
}

.shell.is-collapsed .page-nav a::before {
    content: "⌂";
    font-size: 18px;
}

.page-nav {
    display: grid;
    gap: 6px;
}

.page-nav a {
    padding: 10px 12px;
    border-radius: 8px;
    color: #9aa6b2;
}

.page-nav a.router-link-active {
    background: rgba(91, 140, 255, 0.16);
    color: #edf1f7;
}

.zone-four {
    grid-area: four;
    min-height: 0;
    background: #101216;
}

.main-content {
    display: flex;
    flex-direction: column;
    gap: 18px;
    min-height: 0;
    height: 100%;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 18px 22px 28px;
}

@media (max-width: 760px) {
    .shell,
    .shell.is-collapsed {
        height: 100vh;
        grid-template-columns: 1fr;
        grid-template-rows: 64px 72px minmax(0, 1fr);
        grid-template-areas: "two" "four" "four";
        overflow: hidden;
    }

    .desktop-zone {
        display: none;
    }

    .mobile-expand {
        display: inline-grid;
        place-items: center;
    }

    .zone-two {
        padding: 14px 16px;
        justify-content: flex-start;
    }

    .header-leading {
        width: 100%;
    }

    .header-actions {
        margin-left: auto;
    }

    .mobile-drawer {
        position: fixed;
        inset: 0 auto 0 0;
        z-index: 30;
        width: 248px;
        display: flex;
        flex-direction: column;
        background: #171a21;
        border-right: 1px solid rgba(255, 255, 255, 0.09);
    }

    .mobile-zone {
        border-width: 0 0 1px 0;
    }

    .mobile-zone.zone-one {
        display: flex;
        min-height: 64px;
        padding: 0 16px;
    }

    .mobile-zone.zone-three {
        flex: 1;
        display: flex;
        flex-direction: column;
        gap: 14px;
        padding: 18px 14px;
        overflow-y: auto;
        overflow-x: hidden;
    }

    .mobile-overlay {
        position: fixed;
        inset: 0;
        z-index: 20;
        background: rgba(0, 0, 0, 0.58);
    }

    .page-nav {
        display: grid;
        overflow: visible;
    }

    .page-nav a {
        white-space: nowrap;
    }

    .main-content {
        padding: 14px 16px 22px;
    }

    .shell.is-collapsed .zone-three > .eyebrow {
        display: block;
    }

    .shell.is-collapsed .page-nav a {
        width: auto;
        overflow: visible;
        font-size: inherit;
        text-align: left;
    }

    .shell.is-collapsed .page-nav a::before {
        content: none;
    }

    .shell.is-collapsed .workspace-menu {
        top: 44px;
        left: 0;
    }
}
</style>
