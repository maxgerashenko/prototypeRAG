import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { CallApp } from "./CallApp";
import "./call.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <CallApp />
  </StrictMode>,
);
