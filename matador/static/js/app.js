// matador client behaviours. Loaded as an ES module; each behaviour self-registers
// via document-level event delegation (so it survives htmx swaps) or htmx events.
// Relative imports resolve from this file's URL, so they work at any mount path.
import "./behaviors/theme.js";
import "./behaviors/confirm.js";
import "./behaviors/tooltips.js";
import "./behaviors/bulk-select.js";
import "./behaviors/toast.js";
