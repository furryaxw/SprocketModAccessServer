<script generic="T" lang="ts" setup>
import {onMounted} from "vue";
import {logger} from "../api/logger";

defineProps<{ items: T[] }>();

onMounted(() => logger.debug("component.list.mounted"));
</script>

<template>
    <ul class="ui-list">
        <li v-for="(item, index) in items" :key="index">
            <slot :item="item"/>
        </li>
    </ul>
</template>

<style scoped>
.ui-list {
    display: grid;
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
}

.ui-list > li {
    padding: 12px 14px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 10px;
    background: rgba(255, 255, 255, 0.025);
}
</style>
