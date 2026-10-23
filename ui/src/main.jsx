import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import ErrorBoundary from "./ErrorBoundary.jsx";
import "katex/dist/katex.min.css";
import "./styles.css";

const root = createRoot(document.getElementById("root"));
root.render(
  <ErrorBoundary layer="page" title="AI Librarian could not start">
    <App />
  </ErrorBoundary>,
);
