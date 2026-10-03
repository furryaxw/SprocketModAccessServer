<script lang="ts" setup>
import {nextTick, onBeforeUnmount, onMounted, ref} from "vue";
import {logger} from "../api/logger";
import {useLocale} from "../i18n";

const {setLocale, t} = useLocale();
const languages = [
    {key: "zh" as const, shortKey: "language.options.zh.short", nameKey: "language.options.zh.name"},
    {key: "en" as const, shortKey: "language.options.en.short", nameKey: "language.options.en.name"},
];
const menuOpen = ref(false);
const pickerElement = ref<HTMLElement | null>(null);
const optionElements = ref<HTMLButtonElement[]>([]);

function chooseLocale(nextLocale: "zh" | "en") {
    setLocale(nextLocale);
    menuOpen.value = false;
    logger.info("language_picker.select", {locale: nextLocale});
}

function closeOnOutsideClick(event: MouseEvent) {
    if (!(event.target instanceof Node)) return;
    if (pickerElement.value && !pickerElement.value.contains(event.target)) {
        menuOpen.value = false;
    }
}

function setOptionElement(element: HTMLButtonElement | null, index: number) {
    if (element) optionElements.value[index] = element;
}

function openMenu() {
    menuOpen.value = true;
    void nextTick(() => optionElements.value[0]?.focus());
}

function toggleMenu() {
    if (menuOpen.value) {
        menuOpen.value = false;
    } else {
        openMenu();
    }
}

function handleButtonKeydown(event: KeyboardEvent) {
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openMenu();
    }
}

function handleMenuKeydown(event: KeyboardEvent) {
    if (event.key === "Escape") {
        event.preventDefault();
        menuOpen.value = false;
        pickerElement.value?.querySelector<HTMLButtonElement>(".language-icon")?.focus();
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const options = optionElements.value.filter(Boolean);
    const currentIndex = options.findIndex((element) => element === document.activeElement);
    const step = event.key === "ArrowDown" ? 1 : -1;
    const nextIndex = currentIndex < 0
        ? 0
        : (currentIndex + step + options.length) % options.length;
    options[nextIndex]?.focus();
}

onMounted(() => document.addEventListener("click", closeOnOutsideClick));
onBeforeUnmount(() => document.removeEventListener("click", closeOnOutsideClick));
</script>

<template>
    <div ref="pickerElement" class="language-picker" @click.stop>
        <button
            :aria-label="t('shell.switchLocale')"
            class="language-icon"
            type="button"
            @click="toggleMenu"
            @keydown="handleButtonKeydown"
        >
            <span aria-hidden="true" class="language-back"></span>
            <span class="language-front">{{ t("language.short") }}</span>
        </button>
        <div v-if="menuOpen" class="language-menu" @keydown="handleMenuKeydown">
            <button
                v-for="language in languages"
                :key="language.key"
                :ref="(element) => setOptionElement(element as HTMLButtonElement | null, languages.indexOf(language))"
                type="button"
                @click="chooseLocale(language.key)"
            >
                <span class="language-short">{{ t(language.shortKey) }}</span>
                <span>{{ t(language.nameKey) }}</span>
            </button>
        </div>
    </div>
</template>

<style scoped>
.language-picker {
    position: relative;
    display: inline-block;
}

.language-icon {
    position: relative;
    display: grid;
    width: 48px;
    height: 44px;
    place-items: center;
    border: 0;
    background: transparent;
    color: #5b8cff;
    cursor: pointer;
}

.language-back,
.language-front {
    position: absolute;
    width: 34px;
    height: 30px;
    border: 3px solid #5b8cff;
    border-radius: 10px;
}

.language-back {
    top: 1px;
    left: 9px;
}

.language-front {
    top: 10px;
    left: 1px;
    display: grid;
    place-items: center;
    background: #171a21;
    font-size: 15px;
    font-weight: 600;
}

.language-menu {
    position: absolute;
    top: 48px;
    right: 0;
    z-index: 5;
    display: grid;
    width: 110px;
    padding: 6px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 10px;
    background: #1d212a;
    box-shadow: 0 12px 30px rgba(0, 0, 0, 0.35);
}

.language-menu button {
    display: flex;
    align-items: center;
    gap: 8px;
    border: 0;
    border-radius: 7px;
    padding: 9px 10px;
    background: transparent;
    color: #edf1f7;
    text-align: left;
    cursor: pointer;
}

.language-short {
    min-width: 24px;
    color: #8eacff;
    font-weight: 600;
}

.language-menu button:hover {
    background: rgba(91, 140, 255, 0.16);
}
</style>
