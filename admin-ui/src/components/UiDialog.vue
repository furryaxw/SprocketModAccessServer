<script lang="ts" setup>
import {nextTick, onBeforeUnmount, ref, watch} from "vue";
import {logger} from "../api/logger";
import {useLocale} from "../i18n";

const props = defineProps<{ open: boolean; title: string; busy?: boolean }>();
const emit = defineEmits<{ close: [] }>();
const {t} = useLocale();
const dialogElement = ref<HTMLElement | null>(null);
const backdropHit = ref(false);
let restoreFocusElement: HTMLElement | null = null;

watch(
    () => props.open,
    (open) => {
        logger.debug(open ? "component.dialog.open" : "component.dialog.close");
        if (open) {
            restoreFocusElement = document.activeElement instanceof HTMLElement
                ? document.activeElement
                : null;
            // 键盘事件挂到 document：遮罩点击会把焦点移到 body，只绑在弹窗上会让
            // Escape 与 Tab 陷阱一起失效。
            document.addEventListener("keydown", handleDocumentKeydown, true);
            void nextTick(() => dialogElement.value?.focus());
        } else {
            document.removeEventListener("keydown", handleDocumentKeydown, true);
            restoreFocusElement?.focus();
            restoreFocusElement = null;
        }
    },
);

onBeforeUnmount(() => {
    document.removeEventListener("keydown", handleDocumentKeydown, true);
    restoreFocusElement?.focus();
});

function close() {
    if (props.busy) return;
    logger.debug("component.dialog.dismiss");
    emit("close");
}

function focusableElements(): HTMLElement[] {
    const dialog = dialogElement.value;
    if (!dialog) return [];
    return Array.from(dialog.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]',
    )).filter((element) => element.tabIndex >= 0
        && !element.matches(':disabled, [aria-hidden="true"]')
        && element.getClientRects().length > 0);
}

function handleDocumentKeydown(event: KeyboardEvent) {
    if (!props.open) return;
    if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
    }
    if (event.key !== "Tab") return;
    const dialog = dialogElement.value;
    if (!dialog) return;
    const elements = focusableElements();
    const first = elements[0];
    const last = elements[elements.length - 1];
    const active = document.activeElement;
    if (!first) {
        event.preventDefault();
        dialog.focus();
        return;
    }
    if (!active || !dialog.contains(active)) {
        event.preventDefault();
        (event.shiftKey ? last : first)?.focus();
        return;
    }
    if (event.shiftKey && (active === first || active === dialog)) {
        event.preventDefault();
        last?.focus();
    } else if (!event.shiftKey && (active === last || active === dialog)) {
        event.preventDefault();
        first.focus();
    }
}

function focusDialog() {
    void nextTick(() => {
        dialogElement.value?.focus();
        // 遮罩点击不关闭弹窗（避免误触丢失输入），但必须给出"点击已生效"的反馈，
        // 否则用户会以为没点中。短暂高亮边框即可。
        backdropHit.value = true;
        window.setTimeout(() => {
            backdropHit.value = false;
        }, 600);
    });
}
</script>

<template>
    <div v-if="open" class="dialog-backdrop" @mousedown.self="focusDialog">
        <section
            ref="dialogElement"
            :aria-label="title"
            :aria-busy="busy || undefined"
            :class="{'is-backdrop-hit': backdropHit}"
            aria-modal="true"
            class="dialog"
            role="dialog"
            tabindex="-1"
        >
            <header>
                <h2>{{ title }}</h2>
                <button :aria-label="t('common.close')" :disabled="busy" type="button" class="close" @click="close">×</button>
            </header>
            <div class="body">
                <slot/>
            </div>
            <footer>
                <slot name="footer"/>
            </footer>
        </section>
    </div>
</template>

<style scoped>
.dialog-backdrop {
    position: fixed;
    inset: 0;
    z-index: 20;
    display: grid;
    place-items: center;
    background: rgba(7, 9, 12, 0.7);
    padding: 20px;
}

.dialog {
    display: flex;
    flex-direction: column;
    max-height: calc(100dvh - 40px);
    width: min(560px, 100%);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 16px;
    background: #171a21;
    box-shadow: 0 24px 80px rgba(0, 0, 0, 0.4);
    transition: border-color 200ms ease;
}

.dialog.is-backdrop-hit {
    border-color: rgba(91, 140, 255, 0.6);
}

.dialog header,
.dialog footer {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 18px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.dialog footer {
    justify-content: flex-end;
    border-top: 1px solid rgba(255, 255, 255, 0.08);
    border-bottom: 0;
    gap: 10px;
}

.dialog h2 {
    margin: 0;
    font-size: 18px;
}

.body {
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 18px;
}

.close {
    border: 0;
    background: transparent;
    color: #9aa6b2;
    font-size: 24px;
    cursor: pointer;
}

.close:disabled {
    cursor: wait;
    opacity: 0.5;
}
</style>
