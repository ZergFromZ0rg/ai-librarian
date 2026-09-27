import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "katex/dist/katex.min.css";
import "./styles.css";

const root = createRoot(document.getElementById("root"));
root.render(<App />);
