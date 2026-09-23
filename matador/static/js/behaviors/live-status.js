// The live stream's state, on the root element: `open` once the browser's EventSource
// has actually connected, `error` when it drops, `closed` when it ends. A dashboard
// that stops moving is either quiet or disconnected, and nothing on the page said
// which; this is also what a test waits for before publishing a change, since a change
// published before the subscription lands is delivered to nobody.
const mark = (state) => document.documentElement.setAttribute("data-sse", state);
document.body.addEventListener("htmx:sseOpen", () => mark("open"));
document.body.addEventListener("htmx:sseError", () => mark("error"));
document.body.addEventListener("htmx:sseClose", () => mark("closed"));
