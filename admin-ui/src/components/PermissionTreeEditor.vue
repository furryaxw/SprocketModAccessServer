<script lang="ts" setup>
import {computed, ref, watch} from "vue";
import {useLocale} from "../i18n";
import {
    buildPermissionTree,
    isWildcardNode,
    validateNode,
    type PermissionTreeNode,
} from "../composables/permissionCatalog";

const props = withDefaults(defineProps<{
    // 单一 model：`{节点: effect}`，effect 为 `allow` / `deny`。key 集合就是已选节点，
    // 这样"阻止权限"不需要额外的并行状态。
    modelValue: Record<string, string>;
    options: string[];
    // 用 label 而不是 ariaLabel：`aria-label` 是原生属性名，和 prop 同名会让
    // vue-tsc 把绑定当成普通属性，调用方只能被迫同时传两份。
    label: string;
    // 只有 effect 可写的路径才显示允许/拒绝切换。
    effectsEditable?: boolean;
    // 判定当前账户能否分发该节点。缺省表示"无法判定"，此时不做任何限制。
    grantable?: (node: string) => boolean;
    // 由模板提供的节点：保持已选，但不接受在这里移除（移除不生效反而误导）。
    lockedNodes?: string[];
}>(), {
    effectsEditable: false,
    grantable: undefined,
    lockedNodes: () => [],
});

const emit = defineEmits<{
    "update:modelValue": [value: Record<string, string>];
}>();

type TreeRow = {
    key: string;
    kind: "group" | "leaf";
    depth: number;
    segment: string;
    path: string;
    node: string;
    expandable: boolean;
    expanded: boolean;
    state: "all" | "none" | "some";
    leaves: string[];
    count: number;
    grantable: boolean;
};

type ManualFeedback = {
    tone: "ok" | "warn" | "error";
    text: string;
};

const {t} = useLocale();
const search = ref("");
const manualInput = ref("");
const manualFeedback = ref<ManualFeedback | null>(null);
const expanded = ref<Set<string>>(new Set());
const onlyGrantable = ref(false);
const onlySelected = ref(false);
let initializedOptions = false;

const tree = computed(() => buildPermissionTree(props.options));
const selectedSet = computed(() => new Set(Object.keys(props.modelValue)));
const selectedNodes = computed(() => Object.keys(props.modelValue).sort());
const query = computed(() => search.value.trim().toLowerCase());
const allGroupPaths = computed(() => collectGroupPaths(tree.value));
const hasFilters = computed(() =>
    Boolean(query.value) || onlyGrantable.value || onlySelected.value,
);
// 只有传入了判定函数才提供"只看可分发"；无法判定时不假装有限制。
const hasGrantableScope = computed(() => typeof props.grantable === "function");
const lockedSet = computed(() => new Set(props.lockedNodes));

const rows = computed(() => flatten(tree.value, 0));
const allExpanded = computed(() =>
    allGroupPaths.value.length > 0
    && allGroupPaths.value.every((path) => expanded.value.has(path)),
);

function nodeGrantable(node: string): boolean {
    return props.grantable ? props.grantable(node) : true;
}

function leafVisible(node: string): boolean {
    if (query.value && !node.includes(query.value)) return false;
    if (onlySelected.value && !selectedSet.value.has(node)) return false;
    if (onlyGrantable.value && !nodeGrantable(node)) return false;
    return true;
}

function clearFilters() {
    search.value = "";
    onlyGrantable.value = false;
    onlySelected.value = false;
}

function collectGroupPaths(nodes: readonly PermissionTreeNode[]): string[] {
    const values: string[] = [];
    for (const node of nodes) {
        if (node.kind === "group") {
            values.push(node.path, ...collectGroupPaths(node.children));
        }
    }
    return values;
}

function groupState(leaves: readonly string[]): "all" | "none" | "some" {
    const targets = leaves.map((leaf) => selectedSet.value.has(leaf));
    if (!targets.length) return "none";
    const hit = targets.filter(Boolean).length;
    if (hit === 0) return "none";
    return hit === targets.length ? "all" : "some";
}

function flatten(nodes: readonly PermissionTreeNode[], depth: number): TreeRow[] {
    const values: TreeRow[] = [];
    const autoExpand = Boolean(query.value) || onlyGrantable.value || onlySelected.value;
    for (const node of nodes) {
        if (node.kind === "leaf") {
            if (!leafVisible(node.node)) continue;
            values.push({
                key: node.path,
                kind: "leaf",
                depth,
                segment: node.segment,
                path: node.path,
                node: node.node,
                expandable: false,
                expanded: false,
                state: selectedSet.value.has(node.node) ? "all" : "none",
                leaves: [node.node],
                count: 1,
                grantable: nodeGrantable(node.node),
            });
            continue;
        }
        const children = flatten(node.children, depth + 1);
        // 搜索或筛选中只保留有命中的分组。
        if ((query.value || onlyGrantable.value || onlySelected.value) && !children.length) continue;
        const visibleLeaves = node.leaves.filter(leafVisible);
        const isExpanded = autoExpand || expanded.value.has(node.path);
        values.push({
            key: node.path,
            kind: "group",
            depth,
            segment: node.segment,
            path: node.path,
            node: node.path,
            expandable: true,
            expanded: isExpanded,
            state: groupState(visibleLeaves),
            leaves: visibleLeaves,
            count: visibleLeaves.length,
            grantable: visibleLeaves.some(nodeGrantable),
        });
        if (isExpanded) values.push(...children);
    }
    return values;
}

function labelFrom(key: string, fallback: string): string {
    const value = t(key);
    return value === key ? fallback : value;
}

function segmentLabel(segment: string): string {
    if (segment === "*") return "*";
    return labelFrom(
        `permissionEditor.actions.${segment}`,
        labelFrom(`permissionEditor.resources.${segment}`, segment),
    );
}

const selectedCountText = computed(() =>
    t("permissionEditor.selectedCount").replace("{count}", String(selectedNodes.value.length)),
);

function inCatalog(node: string): boolean {
    return props.options.includes(node);
}

function effectOf(node: string): string {
    return props.modelValue[node] === "deny" ? "deny" : "allow";
}

function emitMap(next: Record<string, string>) {
    emit("update:modelValue", next);
}

function setNode(node: string, effect: string) {
    emitMap({...props.modelValue, [node]: effect});
}

function unsetNode(node: string) {
    const next = {...props.modelValue};
    delete next[node];
    emitMap(next);
}

function toggleEffect(node: string) {
    if (!props.effectsEditable) return;
    setNode(node, effectOf(node) === "deny" ? "allow" : "deny");
}

function toggleExpand(path: string) {
    const next = new Set(expanded.value);
    if (next.has(path)) {
        next.delete(path);
    } else {
        next.add(path);
    }
    expanded.value = next;
}

function toggleExpandAll() {
    expanded.value = allExpanded.value ? new Set() : new Set(allGroupPaths.value);
}

function toggleNode(node: string) {
    if (lockedSet.value.has(node)) return;
    if (selectedSet.value.has(node)) {
        unsetNode(node);
        return;
    }
    // 不可分发的节点不给勾选入口：提交后才被 grant_scope_denied 拒绝是最差的一种反馈。
    if (!nodeGrantable(node)) return;
    setNode(node, "allow");
}

function toggleGroup(row: TreeRow) {
    const deselect = row.state === "all";
    const next = {...props.modelValue};
    for (const leaf of row.leaves) {
        if (lockedSet.value.has(leaf)) continue;
        if (deselect) {
            delete next[leaf];
        } else if (nodeGrantable(leaf)) {
            next[leaf] = next[leaf] ?? "allow";
        }
    }
    emitMap(next);
}

function removeSelected(node: string) {
    unsetNode(node);
}

function issueText(reason: "empty" | "format" | "wildcard" | "noAction"): string {
    return t(`permissionEditor.issue.${reason}`);
}

function addManual() {
    const result = validateNode(manualInput.value);
    if (!result.ok) {
        manualFeedback.value = {tone: "error", text: issueText(result.reason)};
        return;
    }
    if (selectedSet.value.has(result.node)) {
        manualFeedback.value = {tone: "error", text: t("permissionEditor.issue.duplicate")};
        return;
    }
    setNode(result.node, "allow");
    const outside = !inCatalog(result.node);
    manualFeedback.value = {
        tone: outside ? "warn" : "ok",
        text: t(outside ? "permissionEditor.issue.addedOutside" : "permissionEditor.issue.added")
            .replace("{node}", result.node),
    };
    manualInput.value = "";
}

// 目录异步到达：首次拿到目录时展开顶层分组与已选节点的祖先，之后不再覆盖用户的展开状态。
watch(
    () => props.options,
    (options) => {
        if (initializedOptions || !options.length) return;
        initializedOptions = true;
        const paths = new Set(tree.value.filter((node) => node.kind === "group").map((node) => node.path));
        for (const node of selectedNodes.value) {
            const parts = node.split(".");
            for (let index = 1; index < parts.length; index += 1) {
                paths.add(parts.slice(0, index).join("."));
            }
        }
        expanded.value = paths;
    },
    {immediate: true},
);
</script>

<template>
    <fieldset class="permission-tree-editor">
        <legend>{{ label }}</legend>

        <div class="editor-toolbar">
            <input
                v-model="search"
                :aria-label="t('permissionEditor.search')"
                :placeholder="t('permissionEditor.search')"
                class="editor-search"
                type="search"
            />
            <button
                v-if="hasGrantableScope"
                :aria-pressed="onlyGrantable"
                :class="{'is-active': onlyGrantable}"
                class="editor-filter"
                type="button"
                @click="onlyGrantable = !onlyGrantable"
            >
                {{ t("permissionEditor.onlyGrantable") }}
            </button>
            <button
                :aria-pressed="onlySelected"
                :class="{'is-active': onlySelected}"
                class="editor-filter"
                type="button"
                @click="onlySelected = !onlySelected"
            >
                {{ t("permissionEditor.onlySelected") }}
            </button>
            <span class="editor-count">{{ selectedCountText }}</span>
            <button
                v-if="allGroupPaths.length"
                class="editor-link"
                type="button"
                @click="toggleExpandAll"
            >
                {{ allExpanded ? t("permissionEditor.collapseAll") : t("permissionEditor.expandAll") }}
            </button>
        </div>

        <section class="editor-section">
            <h4>{{ t("permissionEditor.tree") }}</h4>
            <ul v-if="rows.length" class="editor-tree">
                <li
                    v-for="row in rows"
                    :key="row.key"
                    :class="{'is-group': row.kind === 'group'}"
                    :data-depth="row.depth"
                    class="editor-row"
                >
                    <template v-if="row.kind === 'group'">
                        <span
                            :aria-expanded="row.expanded"
                            :title="row.expanded ? t('permissionEditor.collapseAll') : t('permissionEditor.expandAll')"
                            class="editor-caret"
                            role="button"
                            tabindex="0"
                            @click="toggleExpand(row.path)"
                            @keydown.enter.prevent="toggleExpand(row.path)"
                            @keydown.space.prevent="toggleExpand(row.path)"
                        >
                            {{ row.expanded ? "▾" : "▸" }}
                        </span>
                        <input
                            :checked="row.state === 'all'"
                            :disabled="row.state !== 'all' && !row.leaves.some(nodeGrantable)"
                            :indeterminate.prop="row.state === 'some'"
                            :aria-label="segmentLabel(row.segment)"
                            :title="row.state === 'all' ? t('permissionEditor.groupNone') : t('permissionEditor.groupAll')"
                            type="checkbox"
                            @change="toggleGroup(row)"
                        />
                        <button
                            class="editor-label-button"
                            type="button"
                            @click="toggleExpand(row.path)"
                        >
                            <span class="editor-label">{{ segmentLabel(row.segment) }}</span>
                            <code class="editor-path">{{ row.path }}</code>
                        </button>
                        <span class="editor-group-count">{{ row.count }}</span>
                    </template>
                    <template v-else>
                        <span aria-hidden="true" class="editor-caret is-placeholder"></span>
                        <input
                            :checked="selectedSet.has(row.node)"
                            :aria-label="row.node"
                            :disabled="lockedSet.has(row.node) || (!row.grantable && !selectedSet.has(row.node))"
                            :title="lockedSet.has(row.node)
                                ? t('permissionEditor.lockedHint')
                                : row.grantable ? row.node : t('permissionEditor.notGrantableHint')"
                            type="checkbox"
                            @change="toggleNode(row.node)"
                        />
                        <span class="editor-label">{{ segmentLabel(row.segment) }}</span>
                        <code class="editor-path">{{ row.node }}</code>
                        <span v-if="lockedSet.has(row.node)" class="editor-tag">
                            {{ t("permissionEditor.locked") }}
                        </span>
                        <span v-if="!row.grantable" class="editor-tag is-warn">
                            {{ t("permissionEditor.notGrantable") }}
                        </span>
                        <span v-if="isWildcardNode(row.node)" class="editor-tag">*</span>
                        <span
                            v-if="selectedSet.has(row.node) && effectOf(row.node) === 'deny'"
                            class="editor-tag is-deny"
                        >
                            {{ t("permissionEditor.effectDeny") }}
                        </span>
                    </template>
                </li>
            </ul>
            <p v-if="hasFilters && !rows.length" class="editor-empty">
                {{ t("permissionEditor.noMatch") }}
                <button class="editor-link" type="button" @click="clearFilters">
                    {{ t("permissionEditor.clearFilters") }}
                </button>
            </p>
            <p v-else-if="!rows.length" class="editor-empty">{{ t("permissionEditor.emptyOptions") }}</p>
        </section>

        <section class="editor-section">
            <h4>{{ t("permissionEditor.selected") }}</h4>
            <div v-if="selectedNodes.length" class="editor-chips">
                <span
                    v-for="node in selectedNodes"
                    :key="node"
                    :data-effect="effectOf(node)"
                    class="editor-chip"
                >
                    <code>{{ node }}</code>
                    <span
                        v-if="lockedSet.has(node)"
                        :title="t('permissionEditor.lockedHint')"
                        class="editor-tag"
                    >
                        {{ t("permissionEditor.locked") }}
                    </span>
                    <span
                        v-if="!inCatalog(node)"
                        :title="t('permissionEditor.notInCatalogHint')"
                        class="editor-tag is-warn"
                    >
                        {{ t("permissionEditor.notInCatalog") }}
                    </span>
                    <button
                        v-if="effectsEditable"
                        :aria-label="`${t('permissionEditor.effectToggle')}: ${node}`"
                        :title="t('permissionEditor.effectToggle')"
                        :data-effect="effectOf(node)"
                        class="editor-effect"
                        type="button"
                        @click="toggleEffect(node)"
                    >
                        {{ effectOf(node) === "deny"
                            ? t("permissionEditor.effectDeny")
                            : t("permissionEditor.effectAllow") }}
                    </button>
                    <button
                        v-if="!lockedSet.has(node)"
                        :aria-label="`${t('permissionEditor.remove')}: ${node}`"
                        class="editor-chip-remove"
                        type="button"
                        @click="removeSelected(node)"
                    >
                        ×
                    </button>
                </span>
            </div>
            <p v-else class="editor-empty">{{ t("permissionEditor.emptySelected") }}</p>
        </section>

        <section class="editor-section is-manual">
            <h4>{{ t("permissionEditor.manual") }}</h4>
            <p class="editor-hint">{{ t("permissionEditor.manualHint") }}</p>
            <div class="editor-manual">
                <input
                    v-model="manualInput"
                    :aria-label="t('permissionEditor.manual')"
                    :placeholder="t('permissionEditor.manualPlaceholder')"
                    class="editor-input"
                    type="text"
                    @keydown.enter.prevent="addManual"
                />
                <button
                    :disabled="!manualInput.trim()"
                    class="editor-action"
                    type="button"
                    @click="addManual"
                >
                    {{ t("permissionEditor.add") }}
                </button>
            </div>
            <p v-if="manualFeedback" :data-tone="manualFeedback.tone" class="editor-feedback">
                {{ manualFeedback.text }}
            </p>
        </section>
    </fieldset>
</template>

<style scoped>
.permission-tree-editor {
    display: grid;
    gap: 14px;
    min-width: 0;
    margin: 0;
    padding: 0;
    border: 0;
}

.permission-tree-editor legend {
    padding: 0;
    color: #9aa6b2;
    font-size: 12px;
}

.editor-toolbar {
    display: flex;
    align-items: center;
    gap: 10px;
}

.editor-search,
.editor-input {
    min-width: 0;
    flex: 1;
    min-height: 38px;
    padding: 8px 11px;
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 8px;
    background: #171a21;
    color: #edf1f7;
    font: inherit;
}

.editor-count {
    flex: 0 0 auto;
    color: #9aa6b2;
    font-size: 12px;
}

.editor-filter {
    flex: 0 0 auto;
    padding: 6px 10px;
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 8px;
    background: transparent;
    color: #9aa6b2;
    font: inherit;
    font-size: 12px;
    cursor: pointer;
}

.editor-filter.is-active {
    border-color: rgba(91, 140, 255, 0.6);
    background: rgba(91, 140, 255, 0.16);
    color: #cfdcff;
}

.editor-link {
    flex: 0 0 auto;
    padding: 0;
    border: 0;
    background: transparent;
    color: #8eacff;
    font: inherit;
    font-size: 12px;
    cursor: pointer;
}

.editor-link:hover {
    text-decoration: underline;
}

.editor-section {
    display: grid;
    gap: 6px;
    min-width: 0;
}

.editor-section h4 {
    margin: 0;
    color: #9aa6b2;
    font-size: 12px;
    font-weight: 600;
}

.editor-tree {
    display: grid;
    margin: 0;
    padding: 4px 0;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    background: #101216;
    list-style: none;
}

.editor-row {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    padding: 5px 10px 5px calc(10px + var(--indent, 0px));
}

.editor-row[data-depth="1"] {
    --indent: 14px;
}

.editor-row[data-depth="2"] {
    --indent: 28px;
}

.editor-row[data-depth="3"] {
    --indent: 42px;
}

.editor-row[data-depth="4"] {
    --indent: 56px;
}

.editor-row:hover {
    background: rgba(255, 255, 255, 0.04);
}

.editor-caret {
    display: inline-grid;
    width: 22px;
    min-width: 22px;
    height: 22px;
    place-items: center;
    border-radius: 6px;
    color: #c8d5ff;
    font-size: 15px;
    line-height: 1;
    cursor: pointer;
    user-select: none;
}

.editor-caret:hover {
    background: rgba(91, 140, 255, 0.18);
}

.editor-caret.is-placeholder {
    cursor: default;
}

.editor-label-button {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    margin-right: auto;
    padding: 2px 4px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    font: inherit;
    text-align: left;
    cursor: pointer;
}

.editor-label-button:hover {
    background: rgba(255, 255, 255, 0.05);
}

.editor-label {
    flex: 0 0 auto;
    color: #edf1f7;
    font-size: 13px;
}

.editor-row.is-group .editor-label {
    color: #c8d5ff;
    font-weight: 600;
}

.editor-path {
    min-width: 0;
    overflow: hidden;
    color: #7d8894;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.editor-group-count {
    flex: 0 0 auto;
    margin-left: auto;
    color: #9aa6b2;
    font-size: 11px;
}

.editor-tag {
    flex: 0 0 auto;
    padding: 1px 6px;
    border-radius: 999px;
    background: rgba(91, 140, 255, 0.16);
    color: #c8d5ff;
    font-size: 11px;
}

.editor-tag.is-warn {
    background: rgba(242, 193, 78, 0.18);
    color: #f2c14e;
}

.editor-tag.is-deny {
    background: rgba(224, 108, 117, 0.2);
    color: #ff9b9b;
}

.editor-chip[data-effect="deny"] {
    border-color: rgba(224, 108, 117, 0.5);
}

.editor-chip[data-effect="deny"] code {
    color: #ff9b9b;
}

.editor-effect {
    padding: 1px 7px;
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 999px;
    background: transparent;
    color: #78d88c;
    font-size: 11px;
    cursor: pointer;
}

.editor-effect[data-effect="deny"] {
    border-color: rgba(224, 108, 117, 0.55);
    color: #ff9b9b;
}

.editor-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
}

.editor-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    max-width: 100%;
    padding: 4px 8px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.04);
}

.editor-chip code {
    min-width: 0;
    overflow: hidden;
    color: #c8d5ff;
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.editor-chip-remove {
    padding: 0 2px;
    border: 0;
    background: transparent;
    color: #9aa6b2;
    font-size: 14px;
    line-height: 1;
    cursor: pointer;
}

.editor-chip-remove:hover {
    color: #ff9b9b;
}

.editor-manual {
    display: flex;
    align-items: center;
    gap: 8px;
}

.editor-action {
    min-height: 38px;
    padding: 0 14px;
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.06);
    color: #edf1f7;
    font: inherit;
    cursor: pointer;
}

.editor-action:disabled {
    cursor: not-allowed;
    opacity: 0.5;
}

.editor-feedback {
    margin: 0;
    font-size: 12px;
}

.editor-feedback[data-tone="ok"] {
    color: #78d88c;
}

.editor-feedback[data-tone="warn"] {
    color: #f2c14e;
}

.editor-feedback[data-tone="error"] {
    color: #ff9b9b;
}

.editor-empty {
    margin: 0;
    color: #9aa6b2;
    font-size: 12px;
}

.editor-hint {
    margin: 0;
    color: #9aa6b2;
    font-size: 12px;
}

.editor-section.is-manual {
    padding-top: 10px;
    border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.editor-search:focus-visible,
.editor-input:focus-visible,
.editor-action:focus-visible,
.editor-caret:focus-visible,
.editor-link:focus-visible,
.editor-chip-remove:focus-visible {
    outline: 2px solid #8eacff;
    outline-offset: 2px;
}
</style>
