import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { useMsw } from "./lib/api";
import "./styles.css";

async function bootstrap() {
  if (useMsw()) {
    const { startMockWorker } = await import("./mocks/browser");
    await startMockWorker();
  }

  const root = document.getElementById("root");
  if (!root) throw new Error("#root missing");
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void bootstrap();
