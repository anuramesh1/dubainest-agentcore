import {
  Component,
  inject,
  signal,
  ElementRef,
  ViewChild,
  afterNextRender,
  effect,
} from "@angular/core";
import { CommonModule } from "@angular/common";
import { FormsModule } from "@angular/forms";
import { I18nService } from "../../services/i18n.service";
import { ChatService, ChatTurn } from "../../services/chat.service";
import { MarkdownResponseComponent } from "../markdown-response/markdown-response.component";

interface Message {
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  suggestions?: string[];
}

@Component({
  selector: "app-chatbot",
  standalone: true,
  imports: [CommonModule, FormsModule, MarkdownResponseComponent],
  templateUrl: "./chatbot.component.html",
  styleUrls: ["./chatbot.component.scss"],
})
export class ChatbotComponent {
  @ViewChild("messagesEl") messagesEl!: ElementRef<HTMLDivElement>;

  i18n = inject(I18nService);
  chatService = inject(ChatService);

  isOpen = this.chatService.isOpen;
  isMaximized = signal(false);
  isTyping = signal(false);
  userInput = signal("");
  messages = signal<Message[]>([]);
  currentPropertyId = signal<string | null>(null);

  // Resize functionality
  windowWidth = signal(384);
  windowHeight = signal(480);
  isResizing = signal(false);
  resizeCorner: "se" | "sw" | "ne" | "nw" = "se";
  resizeStartX = 0;
  resizeStartY = 0;
  resizeStartWidth = 0;
  resizeStartHeight = 0;
  resizeStartTop = 0;
  resizeStartLeft = 0;

  constructor() {
    afterNextRender(() => {
      this.messages.set([
        {
          role: "assistant",
          content: this.i18n.t("chatbot.greeting"),
          timestamp: new Date(),
        },
      ]);
      // Default position: anchored to the bottom-right of the viewport.
      this.position.set({
        top: Math.max(8, window.innerHeight - this.windowHeight() - 96),
        left: Math.max(8, window.innerWidth - this.windowWidth() - 24),
      });

      // Auto-launch the assistant a few seconds after first load to invite the
      // user in. Fires once; skipped if the user has already opened/closed it.
      window.setTimeout(() => {
        if (!this.chatService.userInteracted()) {
          this.isOpen.set(true);
        }
      }, 4000);
    });

    // When opened with a pending message, auto-send it
    effect(() => {
      const msg = this.chatService.pendingMessage();
      if (msg && this.isOpen()) {
        this.chatService.pendingMessage.set(null);
        setTimeout(() => {
          this.userInput.set(msg);
          this.send();
        }, 300);
      }
    });

    // When opened for a specific property, show a templated intro + starter
    // questions instead of auto-sending a "tell me everything" prompt.
    effect(() => {
      const propertyId = this.chatService.pendingIntroPropertyId();
      if (propertyId && this.isOpen()) {
        this.chatService.pendingIntroPropertyId.set(null);
        this.loadPropertyIntro(propertyId);
      }
    });
  }

  private async loadPropertyIntro(propertyId: string) {
    this.currentPropertyId.set(propertyId);
    const data = await this.chatService.fetchPropertyIntro(propertyId);
    if (!data) return;
    // Fresh conversation scoped to this property.
    this.messages.set([
      {
        role: "assistant",
        content: data.intro,
        timestamp: new Date(),
        suggestions: data.suggestions,
      },
    ]);
    this.scrollToBottom();
  }

  /** Send a suggested follow-up question as if the user typed it. */
  sendSuggestion(question: string) {
    if (this.isTyping()) return;
    this.userInput.set(question);
    this.send();
  }

  /** Recent turns (excluding the in-flight placeholder) for multi-turn context. */
  private recentHistory(): ChatTurn[] {
    return this.messages()
      .filter((m) => m.content && m.content !== "Thinking...")
      .slice(-6)
      .map((m) => ({ role: m.role, content: m.content }));
  }

  toggle() {
    this.chatService.toggleOpen();
  }

  toggleMaximize() {
    this.isMaximized.update((v) => !v);
  }

  dragOffsetX = 0;
  dragOffsetY = 0;
  isDragging = false;
  position = signal<{ top: number; left: number }>({ top: 0, left: 0 });

  onDragStart(event: MouseEvent) {
    if (this.isMaximized()) return;
    this.isDragging = true;
    const el = document.getElementById("chatbot-panel");
    if (el) {
      const rect = el.getBoundingClientRect();
      this.dragOffsetX = event.clientX - rect.left;
      this.dragOffsetY = event.clientY - rect.top;
      el.classList.add("dragging");
    }
    document.addEventListener("mousemove", this.onDragMoveBound);
    document.addEventListener("mouseup", this.onDragEndBound);
  }

  onDragMove = (event: MouseEvent) => {
    if (!this.isDragging || this.isMaximized()) return;
    const left = event.clientX - this.dragOffsetX;
    const top = event.clientY - this.dragOffsetY;
    this.position.set({ top: Math.max(0, top), left: Math.max(0, left) });
  };

  onDragEnd = () => {
    this.isDragging = false;
    const el = document.getElementById("chatbot-panel");
    if (el) el.classList.remove("dragging");
    document.removeEventListener("mousemove", this.onDragMoveBound);
    document.removeEventListener("mouseup", this.onDragEndBound);
  };

  onDragMoveBound = (event: MouseEvent) => this.onDragMove(event);
  onDragEndBound = () => this.onDragEnd();

  onResizeStart(event: MouseEvent, corner: "se" | "sw" | "ne" | "nw" = "se") {
    if (this.isMaximized()) return;
    event.preventDefault();
    event.stopPropagation(); // prevent header drag from firing on top corners
    this.isResizing.set(true);
    this.resizeCorner = corner;
    this.resizeStartX = event.clientX;
    this.resizeStartY = event.clientY;
    this.resizeStartWidth = this.windowWidth();
    this.resizeStartHeight = this.windowHeight();
    this.resizeStartTop = this.position().top;
    this.resizeStartLeft = this.position().left;
    document.addEventListener("mousemove", this.onResizeMoveBound);
    document.addEventListener("mouseup", this.onResizeEndBound);
  }

  onResizeMove = (event: MouseEvent) => {
    if (!this.isResizing() || this.isMaximized()) return;
    const dX = event.clientX - this.resizeStartX;
    const dY = event.clientY - this.resizeStartY;
    const minW = 320;
    const minH = 320;

    let newWidth = this.windowWidth();
    let newHeight = this.windowHeight();
    let newTop = this.resizeStartTop;
    let newLeft = this.resizeStartLeft;

    switch (this.resizeCorner) {
      case "se":
        newWidth = Math.max(minW, this.resizeStartWidth + dX);
        newHeight = Math.max(minH, this.resizeStartHeight + dY);
        break;

      case "sw":
        newWidth = Math.max(minW, this.resizeStartWidth - dX);
        // Keep right edge fixed: newLeft = startLeft + startWidth - newWidth
        newLeft = this.resizeStartLeft + this.resizeStartWidth - newWidth;
        newHeight = Math.max(minH, this.resizeStartHeight + dY);
        break;

      case "ne":
        newWidth = Math.max(minW, this.resizeStartWidth + dX);
        newHeight = Math.max(minH, this.resizeStartHeight - dY);
        // Keep bottom edge fixed: newTop = startTop + startHeight - newHeight
        newTop = this.resizeStartTop + this.resizeStartHeight - newHeight;
        break;

      case "nw":
        newWidth = Math.max(minW, this.resizeStartWidth - dX);
        newLeft = this.resizeStartLeft + this.resizeStartWidth - newWidth;
        newHeight = Math.max(minH, this.resizeStartHeight - dY);
        newTop = this.resizeStartTop + this.resizeStartHeight - newHeight;
        break;
    }

    this.windowWidth.set(newWidth);
    this.windowHeight.set(newHeight);
    this.position.set({ top: Math.max(0, newTop), left: Math.max(0, newLeft) });
  };

  onResizeEnd = () => {
    this.isResizing.set(false);
    document.removeEventListener("mousemove", this.onResizeMoveBound);
    document.removeEventListener("mouseup", this.onResizeEndBound);
  };

  onResizeMoveBound = (event: MouseEvent) => this.onResizeMove(event);
  onResizeEndBound = () => this.onResizeEnd();

  async send() {
    const text = this.userInput().trim();
    if (!text || this.isTyping()) return;

    // Capture prior turns BEFORE adding the new question (avoids duplicating it).
    const history = this.recentHistory();

    this.messages.update((msgs) => [
      ...msgs,
      {
        role: "user",
        content: text,
        timestamp: new Date(),
      },
    ]);
    this.userInput.set("");
    this.isTyping.set(true);
    this.scrollToBottom();

    // Stream response from backend /api/chat endpoint
    const tempMessage = {
      role: "assistant" as const,
      content: "",
      timestamp: new Date(),
    };

    this.messages.update((msgs) => [...msgs, tempMessage]);
    const placeholderIndex = this.messages().length - 1;

    await this.chatService.streamChat(
      text,
      this.currentPropertyId(),
      history,
      (evt: any) => {
        const content: string =
          typeof evt?.content === "string" ? evt.content : "";

        const evType: string | null = evt?.event_type ?? null;

        // "suggestions" — follow-up question chips for the just-answered bubble
        if (evType === "suggestions" && Array.isArray(evt?.content)) {
          this.messages.update((msgs) => {
            const updated = [...msgs];
            if (updated[placeholderIndex]) {
              updated[placeholderIndex] = {
                ...updated[placeholderIndex],
                suggestions: evt.content as string[],
              };
            }
            return updated;
          });
          this.scrollToBottom();
          return;
        }

        // "status" — update placeholder text in-place ("Thinking...")
        if (evType === "status") {
          this.messages.update((msgs) => {
            const updated = [...msgs];
            if (updated[placeholderIndex]) {
              updated[placeholderIndex] = {
                ...updated[placeholderIndex],
                content,
              };
            }
            return updated;
          });
          return;
        }

        // "chunk" — stream token into the bubble; hide typing indicator on first chunk
        if (evType === "chunk") {
          if (this.isTyping()) this.isTyping.set(false);
          this.messages.update((msgs) => {
            const updated = [...msgs];
            if (updated[placeholderIndex]) {
              const prev = updated[placeholderIndex].content;
              // Clear "Thinking..." on the very first chunk
              const base = prev === "Thinking..." ? "" : prev;
              updated[placeholderIndex] = {
                ...updated[placeholderIndex],
                content: base + content,
              };
            }
            return updated;
          });
          this.scrollToBottom();
          return;
        }

        // "done" — stream finished; content already built up via chunks
        if (evType === "done") return;

        // "error" — replace placeholder with error message
        if (evType === "error") {
          this.messages.update((msgs) => {
            const updated = [...msgs];
            if (updated[placeholderIndex]) {
              updated[placeholderIndex] = {
                role: "assistant",
                content,
                timestamp: new Date(),
              };
            }
            return updated;
          });
          this.scrollToBottom();
          return;
        }

        // "final" kept for backward compatibility with non-streaming responses
        if (evType === "final") {
          this.messages.update((msgs) => {
            const updated = [...msgs];
            if (updated[placeholderIndex]) {
              updated[placeholderIndex] = {
                role: "assistant",
                content,
                timestamp: new Date(),
              };
            } else {
              updated.push({
                role: "assistant",
                content,
                timestamp: new Date(),
              });
            }
            return updated;
          });
          this.scrollToBottom();
        }
      },
    );

    this.isTyping.set(false);
    this.scrollToBottom();
  }

  onKeydown(e: KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      this.send();
    }
  }

  private scrollToBottom() {
    setTimeout(() => {
      const messagesEl = this.messagesEl?.nativeElement;
      if (messagesEl) {
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
    }, 50);
  }
}
