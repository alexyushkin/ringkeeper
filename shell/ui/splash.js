// What the window shows while Ringkeeper's background service isn't ready.
// The shell sets the state in the address's #fragment.
const STATES = {
  starting: ["Starting Ringkeeper…", "This takes a few seconds.", false],
  restarting: ["Ringkeeper stopped unexpectedly", "Starting it again…", false],
  failed: [
    "Ringkeeper couldn't start",
    "Its background service keeps stopping. Try again, or look at the log in the Ringkeeper data folder (logs\\backend.log).",
    true,
  ],
  stopping: ["Stopping Ringkeeper…", "Finishing up. This window closes when it's done.", false],
};

function show() {
  const [title, detail, retry] = STATES[location.hash.slice(1)] ?? STATES.starting;
  document.getElementById("title").textContent = title;
  document.getElementById("detail").textContent = detail;
  document.getElementById("retry").hidden = !retry;
}

document.getElementById("retry").addEventListener("click", () => {
  location.hash = "starting";
  window.__TAURI__?.core.invoke("retry");
});
window.addEventListener("hashchange", show);
show();
