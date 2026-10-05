import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { VoiceDebugApp } from "./VoiceDebugApp";
import "./voice-debug.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <VoiceDebugApp />
  </StrictMode>,
);
