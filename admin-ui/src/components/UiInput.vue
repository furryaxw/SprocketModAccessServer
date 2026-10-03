<script lang="ts" setup>
import {logger} from "../api/logger";

defineProps<{ modelValue: string; placeholder?: string; ariaLabel?: string }>();
defineEmits<{ "update:modelValue": [value: string] }>();

function logFocus(event: FocusEvent) {
    logger.debug("component.input.focus", {
        label: (event.currentTarget as HTMLInputElement).ariaLabel || undefined,
    });
}

function logBlur(event: FocusEvent) {
    logger.debug("component.input.blur", {
        label: (event.currentTarget as HTMLInputElement).ariaLabel || undefined,
    });
}
</script>

<template>
    <input
        :aria-label="ariaLabel"
        :placeholder="placeholder"
        :value="modelValue"
        class="ui-input"
        @blur="logBlur"
        @focus="logFocus"
        @input="
      $emit('update:modelValue', ($event.target as HTMLInputElement).value)
    "
    />
</template>

<style scoped>
.ui-input {
    width: 100%;
    min-height: 40px;
    padding: 0 12px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 10px;
    outline: none;
    background: rgba(255, 255, 255, 0.04);
    color: #edf1f7;
}

.ui-input:focus {
    border-color: #5b8cff;
    box-shadow: 0 0 0 3px rgba(91, 140, 255, 0.16);
}
</style>
