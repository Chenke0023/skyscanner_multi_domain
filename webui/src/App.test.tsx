import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import App from "./App";

describe("App", () => {
  it("renders the desktop header", async () => {
    render(<App />);
    expect(await screen.findByText("多市场比价与复扫台")).toBeInTheDocument();
    expect(await screen.findByText("查询参数")).toBeInTheDocument();
  });
});
