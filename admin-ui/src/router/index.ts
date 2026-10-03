import {createRouter, createWebHistory} from "vue-router";
import AppShell from "../layouts/AppShell.vue";
import {getSessionToken} from "../api/transport";
import LoginPage from "../pages/LoginPage.vue";
import {logger} from "../api/logger";
import {useSession} from "../composables/useSession";
import OverviewPage from "../pages/OverviewPage.vue";
import UsersPage from "../pages/UsersPage.vue";
import TeamsPage from "../pages/TeamsPage.vue";
import PermissionTemplatesPage from "../pages/PermissionTemplatesPage.vue";
import PermissionAssignmentsPage from "../pages/PermissionAssignmentsPage.vue";
import PackagesPage from "../pages/PackagesPage.vue";
import KeysPage from "../pages/KeysPage.vue";
import OperationsPage from "../pages/OperationsPage.vue";
import ApplicationsPage from "../pages/ApplicationsPage.vue";
import AuditPage from "../pages/AuditPage.vue";

export const router = createRouter({
    // 构建产物挂在 /admin 下，开发服务器从根路径提供页面：base 由 Vite 注入。
    history: createWebHistory(import.meta.env.BASE_URL),
    routes: [
        {path: "/login", name: "login", component: LoginPage},
        {
            path: "/overview",
            name: "overview",
            component: AppShell,
            meta: {requiresAuth: true},
            children: [
                {
                    path: "",
                    name: "overview-content",
                    component: OverviewPage,
                },
                {
                    path: "users",
                    name: "users",
                    component: UsersPage,
                },
                {
                    path: "teams",
                    name: "teams",
                    component: TeamsPage,
                },
                {
                    path: "permission-templates",
                    name: "permission-templates",
                    component: PermissionTemplatesPage,
                },
                {
                    path: "permission-assignments",
                    name: "permission-assignments",
                    component: PermissionAssignmentsPage,
                },
                {
                    path: "packages",
                    name: "packages",
                    component: PackagesPage,
                },
                {
                    path: "keys",
                    name: "keys",
                    component: KeysPage,
                },
                {
                    path: "operations",
                    name: "operations",
                    component: OperationsPage,
                },
                {
                    path: "applications",
                    name: "applications",
                    component: ApplicationsPage,
                },
                {
                    path: "audit",
                    name: "audit",
                    component: AuditPage,
                },
            ],
        },
        {path: "/", redirect: "/overview"},
    ],
});

router.beforeEach(async (to) => {
    const tokenPresent = Boolean(getSessionToken());
    logger.debug("router.before_each", {
        path: to.path,
        name: String(to.name ?? ""),
        tokenPresent,
    });
    if (to.meta.requiresAuth) {
        if (!tokenPresent) {
            logger.info("router.auth.redirect_login", {path: to.path});
            return {name: "login", query: {redirect: to.fullPath}};
        }

        try {
            const session = useSession();
            await session.refresh();
            if (!session.authenticated.value) {
                logger.warn("router.auth.validation_failed", {path: to.path});
                return {name: "login", query: {redirect: to.fullPath}};
            }
        } catch {
            logger.warn("router.auth.invalid_session", {path: to.path});
            return {name: "login", query: {redirect: to.fullPath}};
        }
    }
    if (to.name === "login" && tokenPresent) {
        try {
            const session = useSession();
            await session.refresh();
            if (session.authenticated.value) return {name: "overview"};
        } catch {
            logger.debug("router.auth.login_with_invalid_token");
        }
    }
    return true;
});

router.afterEach((to) => {
    logger.info("router.after_each", {
        path: to.path,
        name: String(to.name ?? ""),
    });
});
