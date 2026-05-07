import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import "./i18n";

if (import.meta.env.DEV && typeof window !== "undefined") {
  const originalWarn = console.warn;
  const originalError = console.error;
  const originalInfo = console.info;
  const originalDebug = console.debug;

  const stringifyConsoleArgs = (args: any[]) =>
    args
      .map((arg) => {
        if (typeof arg === "string") return arg;
        if (arg instanceof Error) return arg.message;
        try {
          return JSON.stringify(arg);
        } catch {
          return String(arg);
        }
      })
      .join(" ");

  const isIgnoredInfoNoise = (msg: string) =>
    msg.includes("i18next is maintained with support from Locize");

  const isIgnoredDebugNoise = (msg: string) => msg.includes("hubos-spark");

  const isIgnoredVendorWarning = (msg: string) =>
    msg.includes(
      "Warning: forwardRef render functions accept exactly two parameters: props and ref.",
    ) ||
    msg.includes(
      "Warning: findDOMNode is deprecated and will be removed in the next major release.",
    ) ||
    msg.includes(
      "Warning: flushSync was called from inside a lifecycle method.",
    ) ||
    msg.includes(
      "Warning: [antd: Card] `bodyStyle` is deprecated. Please use `styles.body` instead.",
    ) ||
    (msg.includes(
      'Warning: Each child in a list should have a unique "key" prop.',
    ) &&
      msg.includes("hubos_chat.js"));

  console.warn = function (...args: any[]) {
    const msg = stringifyConsoleArgs(args);
    if (isIgnoredVendorWarning(msg)) {
      return;
    }
    originalWarn.apply(console, args);
  };

  console.error = function (...args: any[]) {
    const msg = stringifyConsoleArgs(args);
    if (isIgnoredVendorWarning(msg)) {
      return;
    }
    originalError.apply(console, args);
  };

  console.info = function (...args: any[]) {
    const msg = stringifyConsoleArgs(args);
    if (isIgnoredInfoNoise(msg)) {
      return;
    }
    originalInfo.apply(console, args);
  };

  console.debug = function (...args: any[]) {
    const msg = stringifyConsoleArgs(args);
    if (isIgnoredDebugNoise(msg)) {
      return;
    }
    originalDebug.apply(console, args);
  };
}

createRoot(document.getElementById("root")!).render(<App />);
