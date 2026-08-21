import { describe, expect, it } from "vitest";

import { backendErrorDetail, BackendApiError } from "./server-api";

/** A failed backend call as `backendFetch` throws it: the status line lives on
 * `.message`, the parsed backend body on `.data`. */
function failure(status: number, statusText: string, body: unknown) {
  return new BackendApiError(status, statusText, body);
}

describe("backendErrorDetail", () => {
  it("returns the message from the backend's structured error envelope", () => {
    // The motivating case: connecting an MCP plugin under a name already taken.
    const error = failure(409, "Conflict", {
      error: {
        code: "ALREADY_EXISTS",
        message: "MCP connection with this name already exists",
        details: { name: "notion" },
      },
    });

    expect(backendErrorDetail(error)).toBe("MCP connection with this name already exists");
  });

  it("prefers the backend message over a caller fallback", () => {
    const error = failure(404, "Not Found", {
      error: { code: "NOT_FOUND", message: "Conversation not found", details: null },
    });

    expect(backendErrorDetail(error, "Failed to fetch conversation")).toBe(
      "Conversation not found",
    );
  });

  it("reads the string detail FastAPI emits for a raw HTTPException", () => {
    const error = failure(404, "Not Found", { detail: "File not found" });

    expect(backendErrorDetail(error)).toBe("File not found");
  });

  it("joins the per-field messages of a request validation failure", () => {
    // Captured verbatim from POST /api/v1/contact with an invalid body, so the
    // extra keys FastAPI sends (type/loc/input/ctx) are exercised as they arrive.
    const error = failure(422, "Unprocessable Entity", {
      detail: [
        {
          type: "string_too_short",
          loc: ["body", "name"],
          msg: "String should have at least 1 character",
          input: "",
          ctx: { min_length: 1 },
        },
        {
          type: "literal_error",
          loc: ["body", "topic"],
          msg: "Input should be 'support', 'sales', 'partnerships' or 'press'",
          input: "x",
        },
      ],
    });

    expect(backendErrorDetail(error)).toBe(
      "String should have at least 1 character; Input should be 'support', 'sales', 'partnerships' or 'press'",
    );
  });

  it("keeps 5xx generic so an unexpected server error cannot leak internal detail", () => {
    const error = failure(500, "Internal Server Error", {
      error: {
        code: "INTERNAL_ERROR",
        message: "psycopg.OperationalError: connection to 10.0.0.4:5432 failed",
        details: null,
      },
    });

    expect(backendErrorDetail(error, "Failed to create collection")).toBe(
      "Failed to create collection",
    );
    expect(backendErrorDetail(error)).toBe("Backend API error: 500 Internal Server Error");
  });

  it("falls back when the backend sent no parseable body", () => {
    // `backendFetch` passes null when the error body is not JSON.
    const error = failure(502, "Bad Gateway", null);

    expect(backendErrorDetail(error, "Failed to fetch models")).toBe("Failed to fetch models");
    expect(backendErrorDetail(error)).toBe("Backend API error: 502 Bad Gateway");
  });

  it("falls back when a 4xx body carries no usable message", () => {
    const error = failure(400, "Bad Request", { error: { code: "BAD_REQUEST", details: {} } });

    expect(backendErrorDetail(error, "Failed to update conversation")).toBe(
      "Failed to update conversation",
    );
    expect(backendErrorDetail(error)).toBe("Backend API error: 400 Bad Request");
  });

  it("ignores an empty detail array rather than returning an empty toast", () => {
    const error = failure(422, "Unprocessable Entity", { detail: [] });

    expect(backendErrorDetail(error, "Invalid request")).toBe("Invalid request");
  });
});
