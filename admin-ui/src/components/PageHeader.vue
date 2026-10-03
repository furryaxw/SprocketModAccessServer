<script lang="ts" setup>
defineProps<{
    eyebrow: string;
    title: string;
    description: string;
    refreshLabel: string;
    refreshing?: boolean;
}>();

const emit = defineEmits<{
    refresh: [];
}>();
</script>

<template>
    <section class="page-heading">
        <div>
            <p class="eyebrow">{{ eyebrow }}</p>
            <h2>{{ title }}</h2>
            <p class="description">{{ description }}</p>
        </div>
        <button
            class="refresh-button"
            type="button"
            :disabled="refreshing"
            @click="emit('refresh')"
        >
            {{ refreshLabel }}
        </button>
        <slot name="actions"/>
    </section>
</template>

<style scoped>
.page-heading {
    display: flex;
    align-items: flex-start;
    gap: 12px;
}

.page-heading > div:first-child {
    min-width: 0;
    margin-right: auto;
}

.page-heading h2 {
    margin: 0;
    font-size: 28px;
}

.description {
    margin: 8px 0 0;
    color: #9aa6b2;
}

.refresh-button {
    min-height: 38px;
    padding: 0 14px;
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 8px;
    background: #5b8cff;
    color: #fff;
    cursor: pointer;
}

.refresh-button:disabled {
    cursor: wait;
    opacity: 0.55;
}

@media (max-width: 520px) {
    .page-heading {
        flex-wrap: wrap;
    }

    .page-heading > div:first-child {
        flex-basis: 100%;
    }
}
</style>
