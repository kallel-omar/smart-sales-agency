import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";

export function renderApp(initialPath = "/") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <App />
    </MemoryRouter>
  );
}
