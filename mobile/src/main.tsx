import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
// offline app shell for the installable web app (the Android / iOS shells load files locally and don't need it)
if ("serviceWorker" in navigator && location.protocol === "https:" && !location.hostname.endsWith("localhost")) {
  navigator.serviceWorker.register("./sw.js").catch(() => undefined);
}
