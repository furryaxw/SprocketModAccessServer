import {computed, ref} from "vue";
import {logger} from "../api/logger";
import {
    classifyRequestError,
    phaseForFailure,
    RequestFailure,
    type RequestPhase,
} from "./requestState";
import {useLocale} from "../i18n";

export type RequestFetcher<T> = (signal: AbortSignal) => Promise<T>;

export interface RequestOptions<T> {
    isEmpty?: (value: T) => boolean;
}

export function useRequest<T>() {
    const {t} = useLocale();
    const data = ref<T | null>(null);
    const phase = ref<RequestPhase>("idle");
    const error = ref<ReturnType<typeof classifyRequestError> | null>(null);
    let controller: AbortController | null = null;
    let requestVersion = 0;
    let lastFetcher: RequestFetcher<T> | null = null;
    let lastOptions: RequestOptions<T> = {};

    const loading = computed(
        () => phase.value === "loading" || phase.value === "stale",
    );

    async function run(
        fetcher: RequestFetcher<T>,
        options: RequestOptions<T> = {},
    ): Promise<T> {
        controller?.abort();
        const currentController = new AbortController();
        controller = currentController;
        const currentVersion = ++requestVersion;
        lastFetcher = fetcher;
        lastOptions = options;
        error.value = null;
        phase.value = data.value === null ? "loading" : "stale";
        logger.debug("request.start", {hasCachedData: data.value !== null});

        try {
            const result = await fetcher(currentController.signal);
            if (
                currentController.signal.aborted
                || requestVersion !== currentVersion
            ) {
                throw new DOMException(t("common.requestCancelled"), "AbortError");
            }
            data.value = result;
            phase.value = options.isEmpty?.(result) ? "empty" : "success";
            logger.debug("request.success", {empty: phase.value === "empty"});
            return result;
        } catch (cause) {
            const classified = classifyRequestError(cause);
            const failure = currentController.signal.aborted
                ? new RequestFailure(t("common.requestCancelled"), "error", "cancelled")
                : classified;
            if (requestVersion !== currentVersion) {
                throw failure;
            }
            error.value = failure;
            phase.value = phaseForFailure(failure);
            logger.error("request.failed", {
                phase: phase.value,
                code: failure.code,
                message: failure.message,
            });
            throw failure;
        } finally {
            if (controller === currentController) {
                controller = null;
            }
            logger.debug("request.end");
        }
    }

    function cancel() {
        if (!controller) return;
        controller.abort();
        controller = null;
        error.value = null;
        phase.value = "cancelled";
        logger.info("request.cancel");
    }

    async function retry() {
        if (!lastFetcher) return null;
        return run(lastFetcher, lastOptions);
    }

    function markStale() {
        if (data.value !== null) {
            phase.value = "stale";
            error.value = null;
        }
    }

    function reset() {
        controller?.abort();
        controller = null;
        data.value = null;
        error.value = null;
        phase.value = "idle";
        lastFetcher = null;
        lastOptions = {};
        requestVersion += 1;
        logger.debug("request.reset");
    }

    return {
        data,
        phase,
        loading,
        error,
        run,
        retry,
        cancel,
        markStale,
        reset,
    };
}
