import { Injectable, inject, signal } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { environment } from "../../environments/environment";
import { signedJson } from "./signed-body";

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

// Memory keys (spec §7). The session is one chat window: a new one per page
// load. The visitor id is random, stays in this browser, and lets the assistant
// remember preferences (budget, areas) on a later visit. No login, no PII.
const VISITOR_KEY = "dubainest.visitorId";

function newId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function loadVisitorId(): string {
  try {
    const existing = localStorage.getItem(VISITOR_KEY);
    if (existing && /^[A-Za-z0-9-]{8,64}$/.test(existing)) return existing;
    const id = newId();
    localStorage.setItem(VISITOR_KEY, id);
    return id;
  } catch {
    return newId(); // storage blocked: remember for this page only
  }
}

export interface PropertyIntro {
  intro: string;
  suggestions: string[];
}

@Injectable({ providedIn: "root" })
export class ChatService {
  private http = inject(HttpClient);
  private sessionId = newId();
  private visitorId = loadVisitorId();

  /** Start a fresh conversation (the visitor's preferences are kept). */
  newSession() {
    this.sessionId = newId();
  }

  isOpen = signal(false);
  // True once the user has opened/closed the chat themselves. Used to suppress
  // the one-time auto-launch so we never fight a deliberate user action.
  userInteracted = signal(false);
  pendingMessage = signal<string | null>(null);
  pendingPropertyId = signal<string | null>(null);
  // When set, the chatbot opens with a templated property intro + starter buttons.
  pendingIntroPropertyId = signal<string | null>(null);

  /** Open the assistant (e.g. from the navbar launcher). Marks user intent. */
  open() {
    this.userInteracted.set(true);
    this.isOpen.set(true);
  }

  /** Toggle the assistant open/closed. Marks user intent. */
  toggleOpen() {
    this.userInteracted.set(true);
    this.isOpen.update((v) => !v);
  }

  openWithMessage(message: string, propertyId?: string | null) {
    this.userInteracted.set(true);
    this.pendingMessage.set(message);
    this.pendingPropertyId.set(propertyId ?? null);
    this.isOpen.set(true);
  }

  /** Open the chat with a per-property intro + suggested starter questions. */
  openWithIntro(propertyId: string) {
    this.userInteracted.set(true);
    this.pendingIntroPropertyId.set(propertyId);
    this.pendingPropertyId.set(propertyId);
    this.isOpen.set(true);
  }

  async fetchPropertyIntro(propertyId: string): Promise<PropertyIntro | null> {
    const url = `${environment.apiUrl}/api/chat/property-intro/${propertyId}`;
    try {
      const res = await fetch(url);
      if (!res.ok) return null;
      return (await res.json()) as PropertyIntro;
    } catch {
      return null;
    }
  }

  async streamChat(
    prompt: string,
    propertyId?: string | null,
    history?: ChatTurn[],
    onMessage?: (event: any) => void,
  ): Promise<void> {
    const url = `${environment.apiUrl}/api/chat`;
    const payload = {
      prompt,
      property_id: propertyId ?? this.pendingPropertyId() ?? null,
      history: history ?? [],
      session_id: this.sessionId,
      visitor_id: this.visitorId,
    };

    try {
      const { body, headers } = await signedJson(payload);
      const response = await fetch(url, { method: "POST", headers, body });

      if (!response.ok || !response.body) {
        console.error("Chat stream failed:", response.status);
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      // A network read can end mid-event (or mid-character): keep the
      // unfinished last line and prepend it to the next read.
      let pending = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        pending += decoder.decode(value, { stream: true });
        const lines = pending.split("\n");
        pending = lines.pop() ?? "";

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            const dataStr = line.substring(6).trim();
            if (dataStr) {
              try {
                const event = JSON.parse(dataStr);
                if (event && onMessage) {
                  onMessage(event);
                }
              } catch (e) {
                console.error("Failed to parse SSE event:", e);
              }
            }
          }
        }
      }
    } catch (err) {
      console.error("Chat stream error:", err);
    }
  }
}
