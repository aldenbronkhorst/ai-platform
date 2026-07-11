import { describe, expect, it } from "vitest";

import type { ChatMessage } from "../types";
import { applyChatStreamEvent, chatMessageParts, parseSseChunk } from "./runtime";


function pendingMessage(): ChatMessage {
  return {
    id: "assistant-local",
    chat_session_id: "session-1",
    role: "assistant",
    content: "",
    created_at: "2026-07-10T10:00:00Z",
    status: "streaming",
    metadata_json: { request_id: "request-1", status: "streaming", message_parts: [] },
  };
}


function stream(event: string, data: Record<string, unknown>, id = 1) {
  return { id, event, data: { type: event, request_id: "request-1", ...data } };
}


describe("chat stream runtime", () => {
  it("preserves repeated words and reasoning/tool/text order", () => {
    let messages = [pendingMessage()];
    messages = applyChatStreamEvent(messages, stream("reasoning.delta", { text: "Check Odoo first." }, 1));
    messages = applyChatStreamEvent(messages, stream("tool.start", {
      id: "tool-1",
      name: "workspace",
      args: { language: "python" },
    }, 2));
    messages = applyChatStreamEvent(messages, stream("tool.complete", {
      id: "tool-1",
      name: "workspace",
      args: { language: "python" },
      result: { stdout: "connected" },
    }, 3));
    messages = applyChatStreamEvent(messages, stream("message.delta", { text: "Lots " }, 4));
    messages = applyChatStreamEvent(messages, stream("message.delta", { text: "Lots More" }, 5));

    expect(messages[0].content).toBe("Lots Lots More");
    expect(chatMessageParts(messages[0])).toEqual([
      { type: "reasoning", text: "Check Odoo first." },
      {
        type: "tool-call",
        toolCallId: "tool-1",
        toolName: "workspace",
        args: { language: "python" },
        argsText: "",
        result: { stdout: "connected" },
      },
      { type: "text", text: "Lots Lots More" },
    ]);
  });

  it("keeps persisted interleaving when a completed message is reloaded", () => {
    const message: ChatMessage = {
      ...pendingMessage(),
      id: "assistant-final",
      content: "Before tool. After tool.",
      status: "completed",
      metadata_json: {
        request_id: "request-1",
        status: "completed",
        message_parts: [
          { type: "text", text: "Before tool." },
          { type: "tool-call", toolCallId: "tool-1", toolName: "workspace", args: {}, argsText: "" },
          { type: "text", text: " After tool." },
        ],
      },
    };

    expect(chatMessageParts(message).map(part => part.type)).toEqual(["text", "tool-call", "text"]);
    expect(chatMessageParts(message).filter(part => part.type === "text").map(part => part.text).join(""))
      .toBe("Before tool. After tool.");
  });

  it("replaces the optimistic assistant once and keeps replay idempotent", () => {
    const complete = {
      ...pendingMessage(),
      id: "assistant-server",
      content: "Done",
      status: "completed",
      metadata_json: { request_id: "request-1", status: "completed" },
    } satisfies ChatMessage;
    const event = { id: 8, event: "message.complete", data: { ...complete, request_id: "request-1" } };

    const once = applyChatStreamEvent([pendingMessage()], event);
    const twice = applyChatStreamEvent(once, event);
    expect(twice).toHaveLength(1);
    expect(twice[0].id).toBe("assistant-server");
    expect(twice[0].content).toBe("Done");
  });

  it("parses replayable SSE event IDs without altering payload text", () => {
    const parsed = parseSseChunk(
      "id: 42\nevent: message.delta\ndata: {\"request_id\":\"request-1\",\"text\":\"Lots Lots More\"}\n\n",
    );
    expect(parsed.rest).toBe("");
    expect(parsed.events).toEqual([{
      id: 42,
      event: "message.delta",
      data: { request_id: "request-1", text: "Lots Lots More" },
    }]);
  });
});
