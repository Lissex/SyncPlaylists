import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { renderWithClient } from "@/test/render";
import { apiUrl, setupMswServer } from "@/test/server";

import { formatPairingCode, isCompleteCode } from "../lib/code";
import { PairExtensionForm } from "./pair-extension-form";

const server = setupMswServer();

describe("formatPairingCode", () => {
  it.each([
    ["abcd efgh", "ABCD-EFGH"],
    ["ABCD-EFGH", "ABCD-EFGH"],
    ["abc", "ABC"],
    ["ab0o1il", "AB"], // похожие символы в коде не бывают
    ["ABCDEFGHJK", "ABCD-EFGH"],
  ])("%s → %s", (input, expected) => {
    expect(formatPairingCode(input)).toBe(expected);
  });

  it("is complete only with 8 characters", () => {
    expect(isCompleteCode("ABCD-EFG")).toBe(false);
    expect(isCompleteCode("ABCD-EFGH")).toBe(true);
  });
});

describe("PairExtensionForm", () => {
  it("sends the normalized code and reports success", async () => {
    let sent: unknown;
    server.use(
      http.post(apiUrl("/extension/pairings/confirm"), async ({ request }) => {
        sent = await request.json();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const onPaired = vi.fn();
    renderWithClient(<PairExtensionForm onPaired={onPaired} />);

    await userEvent.type(screen.getByLabelText("Код из расширения"), "abcd efgh");
    await userEvent.click(screen.getByRole("button", { name: "Привязать" }));

    await waitFor(() => expect(onPaired).toHaveBeenCalledOnce());
    expect(sent).toEqual({ user_code: "ABCD-EFGH" });
    expect(screen.getByRole("status")).toHaveTextContent("Расширение привязано");
  });

  it("explains an expired code", async () => {
    server.use(
      http.post(apiUrl("/extension/pairings/confirm"), () =>
        HttpResponse.json({ detail: { code: "pairing_not_found" } }, { status: 404 }),
      ),
    );
    renderWithClient(<PairExtensionForm />);

    await userEvent.type(screen.getByLabelText("Код из расширения"), "ABCDEFGH");
    await userEvent.click(screen.getByRole("button", { name: "Привязать" }));

    expect(await screen.findByText(/Код неверный или истёк/)).toBeInTheDocument();
  });

  it("keeps the button disabled until the code is complete", async () => {
    renderWithClient(<PairExtensionForm />);
    await userEvent.type(screen.getByLabelText("Код из расширения"), "ABC");
    expect(screen.getByRole("button", { name: "Привязать" })).toBeDisabled();
  });
});
