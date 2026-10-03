<script lang="ts" setup>
import {ref, toRaw, watch} from "vue";
import {useLocale} from "../i18n";
import UiButton from "./UiButton.vue";
import {
    EXAMPLE_METADATA,
    formatMetadata,
    parseMetadata,
    stripServerOwnedKeys,
    type Metadata,
    type ParseFailure,
} from "../composables/packageMetadata";

// Package metadata 的编辑面：直接编辑 JSON 文档。
//
// 条目形状是嵌套的（localized 文案、安装规则、依赖），逐字段的框表达不了它；这里给一份
// 与公开示例同形的文档起步，保存前摘掉服务端自有的键，其余原样交给服务端。
const props = withDefaults(defineProps<{modelValue: Metadata; disabled?: boolean}>(), {
    disabled: false,
});
const emit = defineEmits<{"update:modelValue": [value: Metadata]}>();

const {t} = useLocale();

const text = ref("");
const failure = ref<ParseFailure | null>(null);
const dropped = ref<string[]>([]);

// 记住自己发出去的那个对象：父组件把它原样放回 model 时不必重新同步（否则输入会被重置）。
// 父组件的 ref 会把发出去的对象包成 reactive 代理，因此比较的是 raw 目标。
let emitted: Metadata | null = null;

function failureText(value: ParseFailure): string {
    if (value.code === "empty") return t("packageMetadata.parseEmpty");
    if (value.code === "object") return t("packageMetadata.parseObject");
    return `${t("packageMetadata.parseSyntax")}${value.detail}`;
}

function commit(value: Metadata) {
    const {metadata, dropped: ignored} = stripServerOwnedKeys(value);
    if (emitted !== null && JSON.stringify(emitted) === JSON.stringify(metadata)) {
        // 文档没变（同一份文本的重复 input）：不改状态、不重复提交。
        return;
    }
    dropped.value = ignored;
    emitted = metadata;
    // 摘掉自有键后文档与输入框不再一致：把真正会保存的文档写回输入框。
    if (ignored.length) text.value = formatMetadata(metadata);
    emit("update:modelValue", metadata);
}

function syncFromModel(value: Metadata) {
    failure.value = null;
    dropped.value = [];
    text.value = formatMetadata(value);
}

function onInput() {
    const result = parseMetadata(text.value);
    if (result.failure) {
        failure.value = result.failure;
        return;
    }
    failure.value = null;
    commit(result.value);
}

function fillExample() {
    text.value = formatMetadata(EXAMPLE_METADATA);
    onInput();
}

function formatJson() {
    const result = parseMetadata(text.value);
    if (result.failure) {
        failure.value = result.failure;
        return;
    }
    failure.value = null;
    text.value = formatMetadata(result.value);
    commit(result.value);
}

watch(
    () => props.modelValue,
    (value) => {
        if (toRaw(value) === emitted) return;
        syncFromModel(value);
    },
    {immediate: true},
);
</script>

<template>
    <fieldset :disabled="disabled" class="package-metadata">
        <legend>{{ t("packageMetadata.section") }}</legend>
        <p class="metadata-hint">{{ t("packageMetadata.jsonHint") }}</p>
        <textarea
            v-model="text"
            :aria-label="t('packageMetadata.section')"
            class="metadata-json"
            rows="18"
            spellcheck="false"
            @input="onInput"
        ></textarea>
        <p v-if="failure" class="metadata-error">{{ failureText(failure) }}</p>
        <p v-if="dropped.length" class="metadata-hint metadata-dropped">
            {{ t("packageMetadata.droppedKeys") }}{{ dropped.join(", ") }}
        </p>
        <div class="metadata-actions">
            <UiButton variant="ghost" @click="fillExample">{{ t("packageMetadata.fillExample") }}</UiButton>
            <UiButton variant="ghost" @click="formatJson">{{ t("packageMetadata.formatJson") }}</UiButton>
        </div>
    </fieldset>
</template>

<style scoped>
.package-metadata {
    display: grid;
    gap: 10px;
    margin: 0;
    padding: 12px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 10px;
}

.package-metadata legend {
    padding: 0 6px;
    color: #9aa6b2;
    font-size: 12px;
}

.metadata-json {
    width: 100%;
    min-height: 320px;
    padding: 10px 12px;
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 8px;
    background: #171a21;
    color: #edf1f7;
    font-family: "Cascadia Mono", Consolas, "Courier New", monospace;
    font-size: 12.5px;
    line-height: 1.55;
    resize: vertical;
    tab-size: 2;
    white-space: pre;
}

.metadata-json:disabled {
    opacity: 0.6;
}

.metadata-actions {
    display: flex;
    gap: 10px;
}

.metadata-hint {
    margin: 0;
    color: #9aa6b2;
    font-size: 12px;
    line-height: 1.5;
}

.metadata-error {
    margin: 0;
    color: #ff9d9d;
    font-size: 12px;
    line-height: 1.5;
    word-break: break-word;
}
</style>
