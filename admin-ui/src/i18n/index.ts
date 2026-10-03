import {computed, reactive} from "vue";
import {en} from "./en";
import {zh} from "./zh";
import {logger} from "../api/logger";

export type LocaleKey = "zh" | "en";
const resources = {zh, en};

const localeStorageKey = "sprocket.access.locale";

function detectLocale(): LocaleKey {
    if (
        typeof navigator !== "undefined" &&
        navigator.language.toLowerCase().startsWith("zh")
    ) {
        return "zh";
    }
    return "en";
}

// 语言选择是用户偏好：先读持久化值，缺失时才按浏览器语言推断。
function initialLocale(): LocaleKey {
    try {
        const stored = localStorage.getItem(localeStorageKey);
        if (stored === "zh" || stored === "en") return stored;
    } catch {
        /* 存储不可用时退回浏览器语言 */
    }
    return detectLocale();
}

const state = reactive({locale: initialLocale()});

function updateDocumentLanguage(locale: LocaleKey) {
    if (typeof document !== "undefined") {
        document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
    }
}

updateDocumentLanguage(state.locale);

function lookup(source: unknown, path: string): unknown {
    return path
        .split(".")
        .reduce<unknown>(
            (value, key) =>
                value && typeof value === "object"
                    ? (value as Record<string, unknown>)[key]
                    : undefined,
            source,
        );
}

export function useLocale() {
    const locale = computed(() => state.locale);
    const t = (path: string) =>
        String(lookup(resources[state.locale], path) ?? lookup(zh, path) ?? path);
    const setLocale = (next: LocaleKey) => {
        logger.info("locale.change", {from: state.locale, to: next});
        state.locale = next;
        updateDocumentLanguage(next);
        try {
            localStorage.setItem(localeStorageKey, next);
        } catch {
            /* 存储不可用时仅保留本次会话的语言 */
        }
    };
    return {locale, t, setLocale};
}
