<script lang="ts" setup>
import {logger} from "../api/logger";

const {
    variant = "secondary",
    type = "button",
    disabled = false,
} = defineProps<{
    variant?: "primary" | "secondary" | "ghost" | "danger";
    type?: "button" | "submit" | "reset";
    disabled?: boolean;
}>();

const emit = defineEmits<{ click: [event: MouseEvent] }>();

function handleClick(event: MouseEvent) {
    logger.debug("component.button.click", {
        variant,
    });
    emit("click", event);
}
</script>

<template>
    <button
        :class="`is-${variant}`"
        :disabled="disabled"
        :type="type"
        class="ui-button"
        @click="handleClick"
    >
        <slot/>
    </button>
</template>

<style scoped>
.ui-button {
    min-height: 40px;
    padding: 0 14px;
    border: 1px solid transparent;
    border-radius: 10px;
    cursor: pointer;
    transition: transform 120ms ease,
    filter 120ms ease;
}

.ui-button:hover:not(:disabled) {
    transform: translateY(-1px);
    filter: brightness(1.08);
}

.ui-button:disabled {
    cursor: not-allowed;
    opacity: 0.5;
}

.is-primary {
    background: #5b8cff;
    color: #fff;
}

.is-secondary {
    background: rgba(255, 255, 255, 0.06);
    border-color: rgba(255, 255, 255, 0.12);
    color: #edf1f7;
}

.is-ghost {
    background: transparent;
    border-color: rgba(255, 255, 255, 0.12);
    color: #edf1f7;
}

.is-danger {
    background: rgba(255, 106, 106, 0.1);
    border-color: rgba(255, 106, 106, 0.45);
    color: #ff9d9d;
}
</style>
