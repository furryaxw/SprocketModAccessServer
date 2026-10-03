<script lang="ts" setup>
import {onMounted} from "vue";
import {logger} from "../api/logger";

export interface ToastItem {
    id: number;
    message: string;
    type: "info" | "success" | "warning" | "error";
}

defineProps<{ items: readonly ToastItem[] }>();

onMounted(() => logger.debug("component.toast_host.mounted"));
</script>

<template>
    <div aria-live="polite" class="toast-host">
        <div
            v-for="item in items"
            :key="item.id"
            :data-type="item.type"
            class="toast"
        >
            {{ item.message }}
        </div>
    </div>
</template>

<style scoped>
.toast-host {
    position: fixed;
    right: 20px;
    bottom: 20px;
    z-index: 30;
    display: grid;
    gap: 10px;
    width: min(320px, calc(100vw - 40px));
}

.toast {
    padding: 12px 14px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 10px;
    background: #1d212a;
    box-shadow: 0 12px 36px rgba(0, 0, 0, 0.32);
}

.toast[data-type="success"] {
    border-color: rgba(52, 199, 89, 0.45);
}

.toast[data-type="error"] {
    border-color: rgba(255, 93, 93, 0.45);
}
</style>
