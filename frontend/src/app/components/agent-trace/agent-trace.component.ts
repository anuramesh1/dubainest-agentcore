import { Component, Input } from "@angular/core";
import { CommonModule } from "@angular/common";

export interface TraceStep {
  kind: string; // guardrail | memory | route | agent | tool | gate
  label: string;
  detail: Record<string, unknown>;
  ms: number;
}

/** The content of the agent's `trace` SSE event (agent/tracing.py). */
export interface AgentTrace {
  intents: string[];
  agent: string;
  tools: string[];
  steps: TraceStep[];
  total_ms: number;
  model_calls: number;
  tokens_in: number;
  tokens_out: number;
}

/** The steps the agent took for one answer: pills, then a closed-by-default list. */
@Component({
  selector: "app-agent-trace",
  standalone: true,
  imports: [CommonModule],
  templateUrl: "./agent-trace.component.html",
  styleUrls: ["./agent-trace.component.scss"],
})
export class AgentTraceComponent {
  @Input({ required: true }) trace!: AgentTrace;

  get seconds(): string {
    return (this.trace.total_ms / 1000).toFixed(1);
  }

  get tokens(): string {
    return (this.trace.tokens_in + this.trace.tokens_out).toLocaleString(
      "en-US",
    );
  }

  /** `{model=nova-micro, confidence=0.95}`; empty, null and false values are left out. */
  detail(step: TraceStep): string {
    const parts = Object.entries(step.detail ?? {})
      .filter(
        ([, v]) => v !== null && v !== undefined && v !== "" && v !== false,
      )
      .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`);
    return parts.length ? `{${parts.join(", ")}}` : "";
  }

  ms(step: TraceStep): string {
    return step.ms ? `${step.ms.toLocaleString("en-US")}ms` : "";
  }
}
