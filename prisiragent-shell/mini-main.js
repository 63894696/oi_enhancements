const fs = require("fs");
const LOG = "/tmp/mini-fs.log";
function log(msg) { fs.appendFileSync(LOG, new Date().toISOString() + " " + msg + "\n"); }
fs.writeFileSync(LOG, "");
log("[mini] enter pid=" + process.pid);
log("[mini] requiring electron");
const { app, BrowserWindow } = require("electron");
log("[mini] required, electron app=" + typeof app);
app.whenReady().then(() => {
  log("[mini] whenReady fired");
  // 不调用 new BrowserWindow,5s 后 quit
  setTimeout(() => { log("[mini] 5s no-BW, quit"); app.quit(); }, 5000);
}).catch((e) => {
  log("[mini] whenReady catch: " + (e && e.stack || e));
});
log("[mini] registered whenReady handler");