<script lang="ts" setup>
import {useLocale} from "../i18n";
import {logger} from "../api/logger";

const props = defineProps<{message?: string; retryLabel?: string}>();
const emit = defineEmits<{ retry: [] }>();
const {t} = useLocale();

function retry() {
    logger.info("component.error_state.retry");
    emit("retry");
}
</script>

<template>
    <div class="state">
        <span>{{ props.message ?? t("state.error") }}</span>
        <button @click="retry">
            {{ props.retryLabel ?? t("state.retry") }}
        </button>
    </div>
</template>

<style scoped>
.state {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 24px;
    color: #ff9b9b;
}

.state button {
    border: 1px solid currentColor;
    border-radius: 8px;
    padding: 6px 10px;
    color: inherit;
    background: transparent;
    cursor: pointer;
}
</style>
