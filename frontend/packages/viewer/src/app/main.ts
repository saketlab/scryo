// Copyright (c) 2025 Apple Inc. Licensed under MIT License.

import { mount } from "svelte";

import "../app.css";

import App from "./Entrypoint.svelte";

const app = mount(App, { target: document.getElementById("app")! });

// both defined in index.html
(window as any).__scryoBootDone?.();
document.getElementById("boot")?.remove();

export default app;
