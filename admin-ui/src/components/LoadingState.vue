<script lang="ts" setup>
import {onMounted} from "vue";
import {logger} from "../api/logger";
import {useLocale} from "../i18n";

const {t} = useLocale();

onMounted(() => logger.debug("component.loading_state.mounted"));
</script>

<template>
    <div class="state">{{ t("state.loading") }}</div>
</template>

<style scoped>
.state {
    padding: 24px;
    color: #9aa6b2;
}
</style>
