import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MicTestApp } from "./MicTestApp";
import "./mic-test.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MicTestApp />
  </StrictMode>,
);
