<script lang="ts" setup>
import {computed} from "vue";
import {logger} from "../api/logger";
import type {RequestPhase} from "../composables/requestState";
import {useLocale} from "../i18n";

const props = withDefaults(
    defineProps<{
        phase: RequestPhase;
        message?: string | null;
        retryLabel?: string;
        cancelLabel?: string;
        cancellable?: boolean;
    }>(),
    {
        message: null,
        retryLabel: undefined,
        cancelLabel: undefined,
        cancellable: false,
    },
);

const emit = defineEmits<{
    retry: [];
    cancel: [];
}>();

const {t} = useLocale();

const visible = computed(() =>
    [
        "loading",
        "stale",
        "empty",
        "forbidden",
        "validation",
        "server-error",
        "error",
        "cancelled",
    ].includes(props.phase),
);

const stateMessage = computed(() => {
    if (props.message) return props.message;
    // 阶段名与消息键只在 server-error 上不一致，其余阶段同名。
    if (props.phase === "server-error") return t("state.serverError");
    return t(`state.${props.phase}`);
});

function retry() {
    logger.info("component.request_state.retry", {phase: props.phase});
    emit("retry");
}

function cancel() {
    logger.info("component.request_state.cancel", {phase: props.phase});
    emit("cancel");
}
</script>

<template>
    <div v-if="visible" :data-phase="phase" class="request-state" role="status">
        <span>{{ stateMessage }}</span>
        <div class="actions">
            <button
                v-if="phase === 'loading' && cancellable"
                class="state-action"
                type="button"
                @click="cancel"
            >
                {{ cancelLabel ?? t("state.cancel") }}
            </button>
            <button
                v-else-if="['error', 'server-error', 'validation'].includes(phase)"
                class="state-action"
                type="button"
                @click="retry"
            >
                {{ retryLabel ?? t("state.retry") }}
            </button>
        </div>
    </div>
</template>

<style scoped>
.request-state {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    min-height: 64px;
    padding: 16px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    color: #9aa6b2;
}

/* 加载态预留一块表格高度：否则首屏从 64px 直接跳到加载完的表格，看起来像页面抽搐。 */
.request-state[data-phase="loading"],
.request-state[data-phase="stale"] {
    min-height: 200px;
}

.request-state[data-phase="forbidden"],
.request-state[data-phase="validation"],
.request-state[data-phase="server-error"],
.request-state[data-phase="error"] {
    color: #ff9b9b;
}

.request-state[data-phase="stale"] {
    color: #c8d5ff;
}

.actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}

.state-action {
    min-height: 34px;
    padding: 0 10px;
    border: 1px solid currentColor;
    border-radius: 6px;
    color: inherit;
    background: transparent;
    cursor: pointer;
}

@media (max-width: 520px) {
    .request-state {
        align-items: flex-start;
        flex-direction: column;
    }
}
</style>
