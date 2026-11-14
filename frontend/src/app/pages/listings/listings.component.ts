import { Component, inject, signal } from "@angular/core";
import { RouterLink } from "@angular/router";
import { FormsModule } from "@angular/forms";
import { toObservable, toSignal } from "@angular/core/rxjs-interop";
import { debounceTime, switchMap } from "rxjs";
import { PropertyService } from "../../services/property.service";
import { I18nService } from "../../services/i18n.service";
import { ChatService } from "../../services/chat.service";
import { Property, PropertyFilters } from "../../models/property.model";

const EMPTY_FILTERS: PropertyFilters = {
  area: "",
  developer: "",
  minPrice: null,
  maxPrice: null,
  type: "",
  bedrooms: null,
};

@Component({
  selector: "app-listings",
  imports: [RouterLink, FormsModule],
  templateUrl: "./listings.component.html",
})
export class ListingsComponent {
  propertyService = inject(PropertyService);
  i18n = inject(I18nService);
  chat = inject(ChatService);

  filters = signal<PropertyFilters>({ ...EMPTY_FILTERS });

  bedroomOptions = [0, 1, 2, 3, 4, 5];

  // Server-side filtered results — re-queries the API whenever filters change.
  properties = toSignal(
    toObservable(this.filters).pipe(
      debounceTime(250),
      switchMap((f) => this.propertyService.search(f)),
    ),
    { initialValue: [] as Property[] },
  );

  areas() {
    return this.propertyService.facets().areas;
  }

  developers() {
    return this.propertyService.facets().developers;
  }

  types() {
    return this.propertyService.facets().types;
  }

  updateFilter(key: keyof PropertyFilters, value: string | number | null) {
    this.filters.update((f) => ({ ...f, [key]: value }));
  }

  clearFilters() {
    this.filters.set({ ...EMPTY_FILTERS });
  }

  formatPrice(price: number): string {
    if (price >= 1_000_000) return `AED ${(price / 1_000_000).toFixed(1)}M`;
    return `AED ${(price / 1000).toFixed(0)}K`;
  }

  hasActiveFilters(): boolean {
    const f = this.filters();
    return !!(
      f.area ||
      f.developer ||
      f.type ||
      f.minPrice ||
      f.maxPrice ||
      f.bedrooms !== null
    );
  }
}
