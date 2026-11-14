import { Routes } from "@angular/router";

export const routes: Routes = [
  {
    path: "",
    loadComponent: () =>
      import("./pages/listings/listings.component").then(
        (m) => m.ListingsComponent,
      ),
  },
  {
    path: "property/:id",
    loadComponent: () =>
      import("./pages/property-detail/property-detail.component").then(
        (m) => m.PropertyDetailComponent,
      ),
  },
  {
    path: "map",
    loadComponent: () =>
      import("./pages/map/map.component").then((m) => m.MapComponent),
  },
  {
    path: "**",
    redirectTo: "",
  },
];
