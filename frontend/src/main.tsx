import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { bootstrapApiToken } from "./api/client";
import "./styles/app.css";

function renderApp(): void {
  createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}

void bootstrapApiToken().then(renderApp, renderApp);
