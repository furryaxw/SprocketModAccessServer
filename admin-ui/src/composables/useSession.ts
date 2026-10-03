import {computed, reactive} from "vue";
import {getSessionToken, setSessionToken, transport} from "../api/transport";
import {logger} from "../api/logger";
import {useAuthorization} from "./useAuthorization";
import {useLocale} from "../i18n";
import {responseFailure} from "./requestState";

const state = reactive({
    token: getSessionToken(),
    user: null as Record<string, unknown> | null,
});

export function useSession() {
    const authorization = useAuthorization();
    const {t} = useLocale();

    // Authentication means the backend has accepted the current session, not
    // merely that a token string exists in browser storage. The authorization
    // snapshot is required before protected UI can resolve its visibility.
    const authenticated = computed(
        () => Boolean(state.token && state.user && authorization.ready.value),
    );

    async function refresh() {
        if (!state.token) {
            logger.debug("session.refresh.skipped", {reason: "missing_token"});
            authorization.reset();
            return null;
        }
        logger.info("session.refresh.start");
        const response = await transport.request({
            action: "read",
            node: "system.authentication.me",
        });
        if (!response.ok) {
            signOut();
            logger.warn("session.refresh.invalid");
            throw responseFailure(response)
                ?? new Error(t("common.sessionInvalid"));
        }
        state.user = response.data ?? null;
        try {
            await authorization.refresh(null);
        } catch (cause) {
            state.user = null;
            logger.warn("session.refresh.authorization_failed", {
                message: cause instanceof Error ? cause.message : String(cause),
            });
            throw cause;
        }
        logger.info("session.refresh.success");
        return state.user;
    }

    function signIn(token: string) {
        state.token = token;
        setSessionToken(token);
        // The GitHub login flow starts on an anonymous socket. After receiving a
        // token, force the next request to reconnect with authenticated transport.
        transport.close();
        logger.info("session.signIn", {tokenPresent: Boolean(token)});
    }

    function signOut() {
        state.token = null;
        state.user = null;
        authorization.reset();
        setSessionToken(null);
        transport.close();
        logger.info("session.signOut");
    }

    return {
        authenticated,
        user: computed(() => state.user),
        authorization,
        refresh,
        signIn,
        signOut,
    };
}
