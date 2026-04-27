import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import App from "./App";

describe("App", () => {
  it("renders the compact desktop shell", async () => {
    render(<App />);
    expect(await screen.findByText("Skyscanner")).toBeInTheDocument();
    expect(await screen.findByText("开始比价")).toBeInTheDocument();
    expect(await screen.findByPlaceholderText("例如：北京")).toBeInTheDocument();
    expect(await screen.findByPlaceholderText("例如：东京")).toBeInTheDocument();
  });
});
