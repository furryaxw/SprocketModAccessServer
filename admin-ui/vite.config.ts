import {defineConfig} from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig(({command}) => ({
    // 构建产物由服务端挂在 /admin 下，开发服务器仍从根路径提供页面。
    base: command === "build" ? "/admin/" : "/",
    plugins: [vue()],
    server: {
        proxy: {
            "/ws": {
                target: "ws://127.0.0.1:8787",
                ws: true,
            },
            "/v1": {
                target: "http://127.0.0.1:8787",
            },
        },
    },
}));
