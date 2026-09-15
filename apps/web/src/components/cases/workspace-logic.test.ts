import { describe, expect, it } from "vitest";

import { parseTags } from "./CaseList";
import { identifierPayload } from "./EntityList";
import { MAX_IMPORT_BYTES, buildImportForm } from "./EvidenceImportForm";
import { describeRunState } from "./RunDetailView";

const baseInput = {
  kind: "text",
  file: null,
  pasted: "",
  filename: "",
  title: "",
  importOrigin: "Provided by the case owner",
  sourceReference: "",
  publishedAt: "",
};

describe("buildImportForm", () => {
  it("requires content and an import origin", () => {
    expect(buildImportForm(baseInput).problem).toMatch(/Choose a file or paste/);
    expect(buildImportForm({ ...baseInput, pasted: "x", importOrigin: " " }).problem).toMatch(/import origin/);
  });

  it("rejects content over the import limit before uploading", () => {
    const file = new File([new Uint8Array(MAX_IMPORT_BYTES + 1)], "big.txt");
    expect(buildImportForm({ ...baseInput, file }).problem).toMatch(/5 MiB/);
  });

  it("builds a multipart body from pasted Turkish text with provenance fields", () => {
    const { form, problem } = buildImportForm({
      ...baseInput,
      pasted: "Görüşme notu: İzmir",
      filename: "not.txt",
      title: "  Interview note ",
      sourceReference: "https://example.org/x",
      publishedAt: "2026-09-01T10:00",
    });
    expect(problem).toBeNull();
    expect(form?.get("kind")).toBe("text");
    expect(form?.get("import_origin")).toBe("Provided by the case owner");
    expect(form?.get("title")).toBe("Interview note");
    expect((form?.get("file") as File).name).toBe("not.txt");
    expect(String(form?.get("source_published_at"))).toMatch(/Z$/);
  });
});

describe("identifierPayload", () => {
  it("drops empty rows and omits blank platforms", () => {
    expect(
      identifierPayload([
        { identifier_type: "username", value: " analyst ", platform: " " },
        { identifier_type: "email", value: "", platform: "" },
        { identifier_type: "platform_id", value: "42", platform: "synthetic-social.example" },
      ]),
    ).toEqual([
      { identifier_type: "username", value: "analyst" },
      { identifier_type: "platform_id", value: "42", platform: "synthetic-social.example" },
    ]);
  });
});

describe("parseTags", () => {
  it("splits and trims comma-separated tags", () => {
    expect(parseTags("altyapı, phishing,, ")).toEqual(["altyapı", "phishing"]);
  });
});

describe("describeRunState", () => {
  it("explains pending dispatch, cancellation and incomplete outcomes honestly", () => {
    expect(describeRunState({ status: "queued", cancel_requested_at: null, dispatch_status: "pending" })).toMatch(
      /dispatcher will retry/,
    );
    expect(describeRunState({ status: "running", cancel_requested_at: "2026-09-15T10:00:00Z", dispatch_status: "dispatched" })).toMatch(
      /Cancellation requested/,
    );
    expect(describeRunState({ status: "partial", cancel_requested_at: null, dispatch_status: "done" })).toMatch(/incomplete coverage/);
    expect(describeRunState({ status: "failed", cancel_requested_at: null, dispatch_status: "done" })).toMatch(/No usable data/);
    expect(describeRunState({ status: "canceled", cancel_requested_at: "x", dispatch_status: "done" })).toMatch(/are kept/);
  });
});
