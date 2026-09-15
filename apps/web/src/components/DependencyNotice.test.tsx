import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DependencyNotice } from "./DependencyNotice";

describe("DependencyNotice", () => {
  it("renders nothing when the API is ready", () => {
    const { container } = render(
      <DependencyNotice
        readiness={{ status: "ready", checks: { database: "ok", migrations: "ok", redis: "ok", storage: "ok" } }}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("lists each failing dependency in words", () => {
    render(
      <DependencyNotice
        readiness={{
          status: "not_ready",
          checks: { database: "ok", migrations: "migrations_pending", redis: "unavailable", storage: "ok" },
        }}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Some required services are unavailable");
    expect(alert).toHaveTextContent("Redis broker: Unavailable");
    expect(alert).toHaveTextContent("Database migrations: Migrations pending");
    expect(alert).not.toHaveTextContent("PostgreSQL database");
  });

  it("distinguishes an unreachable API", () => {
    render(<DependencyNotice readiness={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent("The API is not reachable");
  });
});
