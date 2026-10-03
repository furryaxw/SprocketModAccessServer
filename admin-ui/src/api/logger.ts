const prefix = "[access-ui]";

// Bind native console methods so DevTools attributes each entry to its caller.
export const logger = {
    debug: console.debug.bind(console, prefix),
    info: console.info.bind(console, prefix),
    warn: console.warn.bind(console, prefix),
    error: console.error.bind(console, prefix),
};
