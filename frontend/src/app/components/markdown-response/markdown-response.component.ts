import { Component, Input } from "@angular/core";
import { CommonModule } from "@angular/common";
import { MarkdownModule, provideMarkdown } from "ngx-markdown";

@Component({
  selector: "app-markdown-response",
  standalone: true,
  imports: [CommonModule, MarkdownModule],
  providers: [provideMarkdown()],
  templateUrl: "./markdown-response.component.html",
  styleUrls: ["./markdown-response.component.scss"],
})
export class MarkdownResponseComponent {
  @Input() content: string = "";

  constructor() {}
}
