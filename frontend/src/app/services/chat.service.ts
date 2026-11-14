import { Injectable, inject, signal } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { environment } from "../../environments/environment";

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface PropertyIntro {
  intro: string;
  suggestions: string[];
}

@Injectable({ providedIn: "root" })
export class ChatService {
  private http = inject(HttpClient);

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
    };

    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

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
