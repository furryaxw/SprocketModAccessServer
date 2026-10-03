<script lang="ts" setup>
import {computed} from "vue";
import {useLocale} from "../i18n";

// 有效期控件：以 epoch 秒为模型，界面用本地时间的 datetime-local。
// 空值表示永久有效，避免让使用者手填时间戳。
const props = withDefaults(defineProps<{
    modelValue: number | null;
    label: string;
    disabled?: boolean;
}>(), {
    disabled: false,
});
const emit = defineEmits<{"update:modelValue": [value: number | null]}>();

const {t} = useLocale();

function toLocalInput(seconds: number | null): string {
    if (!seconds) return "";
    const date = new Date(seconds * 1000);
    if (Number.isNaN(date.getTime())) return "";
    const pad = (value: number) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
        `T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

const local = computed({
    get: () => toLocalInput(props.modelValue),
    set: (value: string) => {
        if (!value) {
            emit("update:modelValue", null);
            return;
        }
        const parsed = new Date(value);
        emit("update:modelValue", Number.isNaN(parsed.getTime()) ? null : Math.floor(parsed.getTime() / 1000));
    },
});
</script>

<template>
    <label class="validity-field">
        <span>{{ props.label }}</span>
        <div class="validity-row">
            <input
                v-model="local"
                :aria-label="props.label"
                :disabled="props.disabled"
                type="datetime-local"
            />
            <button
                v-if="props.modelValue"
                :disabled="props.disabled"
                type="button"
                @click="emit('update:modelValue', null)"
            >
                {{ t("validity.clear") }}
            </button>
        </div>
        <em>{{ t("validity.hint") }}</em>
    </label>
</template>

<style scoped>
.validity-field {
    display: grid;
    gap: 6px;
    color: #9aa6b2;
    font-size: 12px;
}

.validity-row {
    display: flex;
    gap: 8px;
    align-items: center;
}

.validity-row input {
    flex: 1;
    min-width: 0;
    min-height: 40px;
    padding: 0 10px;
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 8px;
    background: #171a21;
    color: #edf1f7;
    font: inherit;
    color-scheme: dark;
}

.validity-row button {
    min-height: 40px;
    padding: 0 12px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    background: transparent;
    color: #edf1f7;
    font: inherit;
    cursor: pointer;
}

.validity-field em {
    color: #77808c;
    font-size: 11px;
    font-style: normal;
}
</style>
