import { Injectable, inject, signal } from "@angular/core";
import { HttpClient, HttpParams } from "@angular/common/http";
import { Observable } from "rxjs";
import {
  Property,
  PropertyFilters,
  PropertyFacets,
} from "../models/property.model";
import { environment } from "../../environments/environment";

@Injectable({ providedIn: "root" })
export class PropertyService {
  private http = inject(HttpClient);
  private baseUrl = `${environment.apiUrl}/api/properties`;

  // Full catalogue — backs getAll()/getById() for the map and detail pages.
  private _properties = signal<Property[]>([]);
  private _loaded = signal(false);

  // Filter dropdown options, fetched from the backend facets endpoint.
  private _facets = signal<PropertyFacets>({
    areas: [],
    developers: [],
    types: [],
    bedrooms: [],
  });

  constructor() {
    this.loadProperties();
    this.loadFacets();
  }

  private loadProperties() {
    if (this._loaded()) return;
    this.http.get<Property[]>(this.baseUrl).subscribe({
      next: (data) => {
        this._properties.set(data);
        this._loaded.set(true);
      },
      error: (err) => {
        console.error("Failed to load properties:", err);
        this._loaded.set(true);
      },
    });
  }

  private loadFacets() {
    this.http.get<PropertyFacets>(`${this.baseUrl}/facets`).subscribe({
      next: (data) => this._facets.set(data),
      error: (err) => console.error("Failed to load facets:", err),
    });
  }

  getAll(): Property[] {
    return this._properties();
  }

  getById(id: string): Property | undefined {
    return this._properties().find((p) => p.id === id);
  }

  facets() {
    return this._facets();
  }

  /** Server-side filtered search — sends selections as query params. */
  search(filters: PropertyFilters): Observable<Property[]> {
    let params = new HttpParams();
    if (filters.area) params = params.set("area", filters.area);
    if (filters.developer) params = params.set("developer", filters.developer);
    if (filters.type) params = params.set("type", filters.type);
    if (filters.bedrooms !== null)
      params = params.set("bedrooms", String(filters.bedrooms));
    if (filters.minPrice !== null)
      params = params.set("minPrice", String(filters.minPrice));
    if (filters.maxPrice !== null)
      params = params.set("maxPrice", String(filters.maxPrice));
    return this.http.get<Property[]>(this.baseUrl, { params });
  }
}
