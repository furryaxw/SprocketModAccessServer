<script generic="T extends Record<string, unknown>" lang="ts" setup>
import {onMounted} from "vue";
import {logger} from "../api/logger";

defineProps<{
    columns: Array<{ key: keyof T; label: string }>;
    rows: T[];
}>();

onMounted(() => logger.debug("component.data_table.mounted"));
</script>

<template>
    <div class="table-wrap">
        <table class="ui-table">
            <thead>
            <tr>
                <th v-for="column in columns" :key="String(column.key)">
                    {{ column.label }}
                </th>
            </tr>
            </thead>
            <tbody>
            <tr v-for="(row, index) in rows" :key="index">
                <td v-for="column in columns" :key="String(column.key)">
                    <slot :name="String(column.key)" :row="row">{{
                            row[column.key]
                        }}
                    </slot>
                </td>
            </tr>
            </tbody>
        </table>
    </div>
</template>

<style scoped>
.table-wrap {
    overflow-x: auto;
}

.ui-table {
    width: 100%;
    border-collapse: collapse;
}

th,
td {
    padding: 13px 14px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    text-align: left;
    white-space: nowrap;
}

th {
    color: #9aa6b2;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}
</style>
