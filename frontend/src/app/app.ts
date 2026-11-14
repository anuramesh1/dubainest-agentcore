import { Component, signal } from "@angular/core";
import { RouterOutlet } from "@angular/router";
import { NavbarComponent } from "./components/navbar/navbar.component";
import { ChatbotComponent } from "./components/chatbot/chatbot.component";

@Component({
  selector: "app-root",
  imports: [RouterOutlet, NavbarComponent, ChatbotComponent],
  templateUrl: "./app.html",
  styleUrl: "./app.scss",
})
export class App {
  dir = signal<"ltr" | "rtl">("ltr");

  onLangChange(lang: "en" | "ar") {
    this.dir.set(lang === "ar" ? "rtl" : "ltr");
  }
}
