import { Component, inject, computed, signal } from "@angular/core";
import { ActivatedRoute, RouterLink } from "@angular/router";
import { FormsModule } from "@angular/forms";
import { HttpClient } from "@angular/common/http";
import { toSignal } from "@angular/core/rxjs-interop";
import { map } from "rxjs";
import { PropertyService } from "../../services/property.service";
import { I18nService } from "../../services/i18n.service";
import { ChatService } from "../../services/chat.service";
import { TrustUrlPipe } from "../../pipes/trust-url.pipe";
import { environment } from "../../../environments/environment";

@Component({
  selector: "app-property-detail",
  imports: [RouterLink, FormsModule, TrustUrlPipe],
  templateUrl: "./property-detail.component.html",
})
export class PropertyDetailComponent {
  private route = inject(ActivatedRoute);
  private http = inject(HttpClient);
  propertyService = inject(PropertyService);
  i18n = inject(I18nService);
  chatService = inject(ChatService);

  private id = toSignal(
    this.route.paramMap.pipe(map((p) => p.get("id") ?? "")),
  );
  property = computed(() => this.propertyService.getById(this.id() ?? ""));

  showEnquiry = signal(false);
  enquiryForm = signal({ name: "", email: "", phone: "", message: "" });
  enquirySent = signal(false);

  formatPrice(price: number): string {
    if (price >= 1_000_000) return `AED ${(price / 1_000_000).toFixed(2)}M`;
    return `AED ${(price / 1000).toFixed(0)}K`;
  }

  estimatedAnnualRent(price: number, roi: number): string {
    return `AED ${Math.round((price * roi) / 100).toLocaleString()}`;
  }

  openEnquiry() {
    this.showEnquiry.set(true);
    this.enquirySent.set(false);
  }

  closeEnquiry() {
    this.showEnquiry.set(false);
  }

  submitEnquiry() {
    const form = this.enquiryForm();
    const propertyId = this.id();
    if (
      !form.name ||
      !form.email ||
      !form.phone ||
      !form.message ||
      !propertyId
    ) {
      console.error("Incomplete form");
      return;
    }

    const payload = {
      property_id: propertyId,
      name: form.name,
      email: form.email,
      phone: form.phone,
      message: form.message,
    };

    this.http.post(`${environment.apiUrl}/api/enquiries`, payload).subscribe({
      next: () => {
        this.enquirySent.set(true);
        setTimeout(() => this.closeEnquiry(), 2000);
      },
      error: (err) => {
        console.error("Enquiry submission failed:", err);
      },
    });
  }

  updateField(field: string, value: string) {
    this.enquiryForm.update((f) => ({ ...f, [field]: value }));
  }

  askAI() {
    const p = this.property();
    const id = this.id();
    if (!p || !id) return;
    // Open the chat with a short intro + suggested starter questions instead of
    // dumping a full brief — the user drives the conversation from there.
    this.chatService.openWithIntro(id);
  }

  openMap(lat: number, lng: number) {
    window.open(`https://www.google.com/maps?q=${lat},${lng}`, "_blank");
  }

  getMapEmbedUrl(lat: number, lng: number): string {
    const delta = 0.008;
    return `https://www.openstreetmap.org/export/embed.html?bbox=${lng - delta},${lat - delta},${lng + delta},${lat + delta}&layer=mapnik&marker=${lat},${lng}`;
  }
}
