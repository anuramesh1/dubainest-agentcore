import { Component, inject, output } from "@angular/core";
import { RouterLink, RouterLinkActive } from "@angular/router";
import { I18nService, Lang } from "../../services/i18n.service";

@Component({
  selector: "app-navbar",
  imports: [RouterLink, RouterLinkActive],
  templateUrl: "./navbar.component.html",
})
export class NavbarComponent {
  i18n = inject(I18nService);
  langChange = output<Lang>();

  toggleLang() {
    const next: Lang = this.i18n.lang() === "en" ? "ar" : "en";
    this.i18n.setLang(next);
    this.langChange.emit(next);
  }
}
