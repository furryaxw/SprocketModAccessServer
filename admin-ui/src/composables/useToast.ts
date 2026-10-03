import {reactive, readonly} from "vue";
import {logger} from "../api/logger";

export type ToastType = "info" | "success" | "warning" | "error";

export interface ToastItem {
    id: number;
    message: string;
    type: ToastType;
}

const state = reactive<{ nextId: number; items: ToastItem[] }>({
    nextId: 1,
    items: [],
});

export function useToast() {
    function push(message: string, type: ToastType = "info") {
        const item = {id: state.nextId++, message, type};
        state.items.push(item);
        logger.info("toast.show", {type});
        window.setTimeout(() => {
            const index = state.items.findIndex((entry) => entry.id === item.id);
            if (index >= 0) {
                state.items.splice(index, 1);
                logger.debug("toast.dismiss", {type});
            }
        }, 2600);
    }

    return {items: readonly(state.items), push};
}
