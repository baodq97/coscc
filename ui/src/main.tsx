import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { match, usePath } from "./lib/router";
import { SCREENS } from "./routes";
import { Shell } from "./shell/Shell";
import { Empty } from "./components/ui";

function App() {
  const path = usePath();
  for (const s of SCREENS) {
    const params = match(s.path, path);
    if (params)
      return (
        <Shell title={s.title} crumbs={() => (s.crumbs ? s.crumbs(params) : [s.title])}>
          {s.render(params)}
        </Shell>
      );
  }
  return (
    <Shell title="Not found" crumbs={() => ["Not found"]}>
      <div className="page">
        <Empty icon="search" title="Nothing at this address">
          The link may be old. Ask Leif with <kbd>⌘K</kbd>, or go to the briefing.
        </Empty>
      </div>
    </Shell>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
