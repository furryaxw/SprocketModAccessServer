<script lang="ts" setup>
import {ref} from "vue";
import {useRouter} from "vue-router";
import ErrorState from "../components/ErrorState.vue";
import LanguagePicker from "../components/LanguagePicker.vue";
import LoadingState from "../components/LoadingState.vue";
import UiButton from "../components/UiButton.vue";
import {transport, type TransportResponse} from "../api/transport";
import {logger} from "../api/logger";
import {useSession} from "../composables/useSession";
import {useToast} from "../composables/useToast";
import {useLocale} from "../i18n";
import {responseFailure} from "../composables/requestState";

const {locale, t: baseT} = useLocale();
const loginText = {
    zh: {
        title: "登录",
        description: "使用 GitHub 登录以访问你被授权的工作区。",
        github: "使用 GitHub 继续",
        deviceTitle: "GitHub 设备登录",
        deviceCode: "输入代码",
        deviceOpen: "打开 GitHub 验证页",
        failed: "无法启动 GitHub 登录。",
        unavailable: "服务器未启用 GitHub 登录。",
        popupBlocked: "请允许弹窗后继续 GitHub 登录。",
        popupClosed: "GitHub 登录窗口已关闭。",
        timeout: "登录回调超时。",
    },
    en: {
        title: "Sign in",
        description: "Authenticate with GitHub to access your authorized workspaces.",
        github: "Continue with GitHub",
        deviceTitle: "GitHub device login",
        deviceCode: "Enter code",
        deviceOpen: "Open GitHub verification",
        failed: "Unable to start GitHub login.",
        unavailable: "GitHub login is not enabled on this server.",
        popupBlocked: "Allow popups to continue with GitHub.",
        popupClosed: "The GitHub login window was closed.",
        timeout: "The login callback timed out.",
    },
} as const;
const pageT = (key: keyof (typeof loginText)["en"]) =>
    loginText[locale.value][key];
const {signIn, refresh, signOut} = useSession();
const toast = useToast();
const router = useRouter();
const loading = ref(false);
const error = ref("");
const deviceUserCode = ref("");
const deviceVerificationUri = ref("");

type GitHubAuthMethods = {
    web?: boolean;
    device?: boolean;
};

function responseMessage(response: TransportResponse, fallback: string) {
    return responseFailure(response)?.message ?? fallback;
}

function callbackErrorMessage(error: unknown) {
    if (!error || typeof error !== "object") return pageT("failed");
    const value = error as {code?: unknown};
    const code = typeof value.code === "string" ? value.code : "";
    const translated = code ? baseT(`common.errors.${code}`) : "";
    return translated && translated !== `common.errors.${code}`
        ? translated
        : pageT("failed");
}

async function loginWithGitHub() {
    logger.info("login.start");
    loading.value = true;
    error.value = "";
    deviceUserCode.value = "";
    deviceVerificationUri.value = "";
    try {
        const methods = await githubAuthMethods();
        logger.debug("login.methods", methods);
        if (methods.web) {
            await loginWithGitHubWeb();
        } else if (methods.device) {
            await loginWithGitHubDevice();
        } else {
            throw new Error(pageT("unavailable"));
        }
    } catch (cause) {
        logger.error("login.failed", {
            message: cause instanceof Error ? cause.message : String(cause),
        });
        error.value = cause instanceof Error ? cause.message : pageT("failed");
        toast.push(error.value, "error");
    } finally {
        loading.value = false;
    }
}

async function githubAuthMethods(): Promise<GitHubAuthMethods> {
    const response = await transport.request({
        action: "read",
        node: "system.server_info",
    });
    if (!response.ok) {
        throw new Error(responseMessage(response, pageT("failed")));
    }
    const methods = response.data?.identity_policy as
        | {auth_methods?: {github?: GitHubAuthMethods}}
        | undefined;
    return methods?.auth_methods?.github ?? {web: false, device: false};
}

async function loginWithGitHubWeb() {
    const response = await transport.request({
        action: "start",
        node: "system.authentication.github.web",
    });
    logger.debug("login.web_flow.response", {ok: response.ok});
    if (!response.ok) {
        throw new Error(responseMessage(response, pageT("failed")));
    }
    const url = String(response.data?.authorization_url ?? "");
    const callbackUrl = String(
        response.data?.callback_url ?? window.location.origin,
    );
    if (!url) {
        throw new Error(pageT("failed"));
    }
    const popup = window.open(
        url,
        "sprocket-github-login",
        "width=640,height=760",
    );
    if (!popup) {
        throw new Error(pageT("popupBlocked"));
    }

    const callbackOrigin = new URL(
        callbackUrl,
        window.location.origin,
    ).origin;
    await new Promise<void>((resolve, reject) => {
        const closeCheck = window.setInterval(() => {
            if (!popup.closed) return;
            window.clearInterval(closeCheck);
            window.clearTimeout(timeout);
            window.removeEventListener("message", onMessage);
            reject(new Error(pageT("popupClosed")));
        }, 500);
        const timeout = window.setTimeout(() => {
            window.clearInterval(closeCheck);
            window.removeEventListener("message", onMessage);
            reject(new Error(pageT("timeout")));
        }, 120000);

        function onMessage(event: MessageEvent) {
            // Fix: accept callback messages only from the configured callback
            // origin and the popup opened by this page.
            if (event.origin !== callbackOrigin || event.source !== popup) {
                return;
            }
            window.clearTimeout(timeout);
            window.clearInterval(closeCheck);
            window.removeEventListener("message", onMessage);
            if (event.data?.ok === false) {
                reject(new Error(callbackErrorMessage(event.data?.error)));
                return;
            }
            const token = event.data?.payload?.token;
            if (typeof token !== "string") {
                reject(new Error(pageT("failed")));
                return;
            }
            acceptSessionToken(token).then(resolve).catch(reject);
        }

        window.addEventListener("message", onMessage);
    });
}

async function loginWithGitHubDevice() {
    const response = await transport.request({
        action: "start",
        node: "system.authentication.github.device",
    });
    logger.debug("login.device_flow.response", {ok: response.ok});
    if (!response.ok) {
        throw new Error(responseMessage(response, pageT("failed")));
    }
    const flowId = String(response.data?.flow_id ?? "");
    const userCode = String(response.data?.user_code ?? "");
    const verificationUri = String(response.data?.verification_uri ?? "");
    const intervalSeconds = Math.max(2, Number(response.data?.interval ?? 5));
    const expiresIn = Math.max(60, Number(response.data?.expires_in ?? 900));
    if (!flowId || !userCode || !verificationUri) {
        throw new Error(pageT("failed"));
    }
    deviceUserCode.value = userCode;
    deviceVerificationUri.value = verificationUri;

    // Device flow is asynchronous: GitHub returns pending until the user approves
    // the code in another browser tab, then the server returns the session token.
    const deadline = Date.now() + expiresIn * 1000;
    while (Date.now() < deadline) {
        await delay(intervalSeconds * 1000);
        const poll = await transport.request({
            action: "poll",
            node: "system.authentication.github.device",
            data: {flow_id: flowId},
        });
        if (!poll.ok) {
            throw new Error(responseMessage(poll, pageT("failed")));
        }
        if (poll.data?.status === "pending") {
            continue;
        }
        const token = poll.data?.token;
        if (typeof token !== "string") {
            throw new Error(pageT("failed"));
        }
        await acceptSessionToken(token);
        return;
    }
    throw new Error(pageT("timeout"));
}

async function acceptSessionToken(token: string) {
    signIn(token);
    logger.info("login.callback.received");
    try {
        const user = await refresh();
        if (!user) {
            throw new Error(pageT("failed"));
        }
        logger.info("login.session.validated");
        await router.push("/overview");
    } catch (cause) {
        signOut();
        logger.warn("login.session.invalid", {
            message: cause instanceof Error ? cause.message : String(cause),
        });
        throw new Error(pageT("failed"));
    }
}

function delay(milliseconds: number) {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
</script>

<template>
    <main class="login-page">
        <section class="login-card">
            <div class="login-brand-row">
                <div class="brand-mark">{{ baseT("app.shortName") }}</div>
                <LanguagePicker/>
            </div>
            <p class="eyebrow">{{ baseT("app.name") }}</p>
            <h1>{{ pageT("title") }}</h1>
            <p class="description">{{ pageT("description") }}</p>
            <div v-if="deviceUserCode" class="device-flow">
                <p>{{ pageT("deviceTitle") }}</p>
                <strong>{{ pageT("deviceCode") }}: {{ deviceUserCode }}</strong>
                <a :href="deviceVerificationUri" target="_blank" rel="noreferrer">
                    {{ pageT("deviceOpen") }}
                </a>
            </div>
            <LoadingState v-if="loading"/>
            <ErrorState
                v-else-if="error"
                :message="error"
                :retry-label="baseT('state.retry')"
                @retry="loginWithGitHub"
            />
            <UiButton v-else variant="primary" @click="loginWithGitHub">
                {{ pageT("github") }}
            </UiButton>
        </section>
    </main>
</template>

<style scoped>
.login-page {
    min-height: 100vh;
    display: grid;
    place-items: center;
    padding: 24px;
    background: #0f1115;
}

.login-card {
    position: relative;
    width: min(100%, 420px);
    display: grid;
    gap: 16px;
    padding: 36px;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 18px;
    background: #171a21;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.login-brand-row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
}

.brand-mark {
    display: grid;
    width: 48px;
    height: 48px;
    place-items: center;
    border-radius: 12px;
    background: #5b8cff;
    font-weight: 700;
}

h1 {
    margin: 0;
    font-size: 28px;
}

.description {
    margin: 0;
    color: #9aa6b2;
    line-height: 1.6;
}

.eyebrow {
    margin: 0;
    color: #9aa6b2;
    font-size: 12px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}

.device-flow {
    display: grid;
    gap: 8px;
    padding: 12px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    background: #20242d;
}

.device-flow p {
    margin: 0;
    color: #d8dee9;
}

.device-flow strong {
    font-size: 18px;
    letter-spacing: 0.08em;
}

.device-flow a {
    color: #8fb1ff;
}
</style>
