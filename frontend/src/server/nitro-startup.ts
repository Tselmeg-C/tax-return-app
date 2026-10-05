// Nitro plugin: runs once when the server starts (the route modules load lazily, on the
// first request), so configuration warnings show up in the deploy log right away.
import { definePlugin } from "nitro";

import { logStartup } from "./startup-checks";

export default definePlugin(() => {
  logStartup(process.env);
});
