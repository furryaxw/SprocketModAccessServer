import {createApp} from "vue";
import App from "./App.vue";
import {router} from "./router";
import "./theme/base.css";
import {logger} from "./api/logger";

const app = createApp(App);
window.addEventListener("error", (event) => {
    logger.error("window.error", {
        message: event.message,
        filename: event.filename,
        line: event.lineno,
    });
});
window.addEventListener("unhandledrejection", (event) => {
    logger.error("window.unhandled_rejection", {
        reason: String(event.reason),
    });
});
app.config.errorHandler = (error, _instance, info) => {
    logger.error("vue.error", {error, info});
};
router.onError((error, to) => {
    logger.error("router.error", {error, path: to.fullPath});
});
app.use(router).mount("#app");
logger.info("app.mounted");
