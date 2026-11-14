import {
  Component,
  inject,
  OnDestroy,
  AfterViewInit,
  ElementRef,
  ViewChild,
} from "@angular/core";
import * as L from "leaflet";
import { PropertyService } from "../../services/property.service";
import { I18nService } from "../../services/i18n.service";
import { Property } from "../../models/property.model";

// Fix Leaflet default marker icon paths broken by webpack
const iconDefault = L.icon({
  iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  iconRetinaUrl:
    "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
});

const damacIcon = L.divIcon({
  className: "",
  html: `<div style="background:#1d4ed8;color:white;border-radius:50%;width:36px;height:36px;display:flex;align-items:center;justify-content:center;font-size:16px;box-shadow:0 2px 8px rgba(0,0,0,0.3);border:2px solid white;">🏢</div>`,
  iconSize: [36, 36],
  iconAnchor: [18, 18],
  popupAnchor: [0, -20],
});

// Dubai area overlays: [name, lat, lng, radiusMeters, color]
const AREA_OVERLAYS: [string, number, number, number, string][] = [
  ["Downtown Dubai", 25.1972, 55.2744, 1200, "#3b82f6"],
  ["Dubai Marina", 25.0805, 55.1403, 1000, "#8b5cf6"],
  ["Palm Jumeirah", 25.1124, 55.139, 1800, "#f59e0b"],
  ["Business Bay", 25.1865, 55.2695, 1000, "#10b981"],
  ["MBR City", 25.1773, 55.3079, 1500, "#ef4444"],
  ["Dubai Hills", 25.1018, 55.2464, 1200, "#ec4899"],
  ["Meydan", 25.175, 55.315, 1000, "#14b8a6"],
  ["Bluewaters", 25.0806, 55.1216, 600, "#f97316"],
  ["Maritime City", 25.2666, 55.2659, 800, "#6366f1"],
];

@Component({
  selector: "app-map",
  template: `
    <div class="flex flex-col h-[calc(100vh-64px)]">
      <!-- Toolbar -->
      <div
        class="bg-white border-b border-gray-200 px-4 py-2 flex items-center gap-4 z-10 shadow-sm flex-shrink-0"
      >
        <span class="font-semibold text-gray-800 text-sm">🗺️ Property Map</span>
        <span class="text-xs text-gray-400"
          >{{ propertyService.getAll().length }} properties · click a pin for
          details</span
        >
        <div class="ml-auto flex gap-3 text-xs text-gray-500">
          <span class="flex items-center gap-1"
            ><span class="inline-block w-3 h-3 rounded-full bg-blue-500"></span>
            Area zones</span
          >
          <span class="flex items-center gap-1">🏢 Properties</span>
        </div>
      </div>

      <!-- Map container -->
      <div #mapEl class="flex-1 w-full z-0"></div>
    </div>
  `,
})
export class MapComponent implements AfterViewInit, OnDestroy {
  @ViewChild("mapEl") mapEl!: ElementRef<HTMLDivElement>;

  propertyService = inject(PropertyService);
  i18n = inject(I18nService);

  private map!: L.Map;

  ngAfterViewInit() {
    this.initMap();
  }

  ngOnDestroy() {
    this.map?.remove();
  }

  private initMap() {
    this.map = L.map(this.mapEl.nativeElement, {
      center: [25.1412, 55.22],
      zoom: 11,
      zoomControl: true,
    });

    // OpenStreetMap tiles
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution:
        '© <a href="https://openstreetmap.org">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(this.map);

    // Area overlay circles
    AREA_OVERLAYS.forEach(([name, lat, lng, radius, color]) => {
      L.circle([lat, lng], {
        radius,
        color,
        fillColor: color,
        fillOpacity: 0.08,
        weight: 1.5,
        opacity: 0.5,
      })
        .bindTooltip(name, {
          permanent: false,
          direction: "top",
          className: "area-tooltip",
        })
        .addTo(this.map);
    });

    // Property markers
    this.propertyService.getAll().forEach((p) => this.addMarker(p));
  }

  private addMarker(p: Property) {
    const lang = this.i18n.lang();
    const title = lang === "ar" ? p.titleAr : p.title;
    const area = lang === "ar" ? p.areaAr : p.area;
    const price =
      p.price >= 1_000_000
        ? `AED ${(p.price / 1_000_000).toFixed(2)}M`
        : `AED ${(p.price / 1000).toFixed(0)}K`;

    const roiBadge =
      p.roi > 0
        ? `<span style="background:#10b981;color:white;border-radius:9999px;padding:2px 8px;font-size:11px;font-weight:600;">${p.roi}% ROI</span>`
        : `<span style="background:#6b7280;color:white;border-radius:9999px;padding:2px 8px;font-size:11px;font-weight:600;">Off-Plan ${p.completionYear}</span>`;

    const popup = `
      <div style="width:220px;font-family:system-ui,sans-serif;">
        <img src="${p.images[0]}" style="width:100%;height:110px;object-fit:cover;border-radius:8px;margin-bottom:8px;" />
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:4px;margin-bottom:4px;">
          <p style="font-weight:600;font-size:13px;color:#111;line-height:1.3;margin:0;">${title}</p>
          ${roiBadge}
        </div>
        <p style="font-size:11px;color:#6b7280;margin:0 0 6px;">📍 ${area} · <strong style="color:#1d4ed8;">${price}</strong></p>
        <p style="font-size:11px;color:#374151;margin:0 0 8px;">🛏 ${p.bedrooms === 0 ? "Studio" : p.bedrooms + "BR"} &nbsp;🚿 ${p.bathrooms} &nbsp;📐 ${p.sizeSqft.toLocaleString()} sqft</p>
        <a href="/property/${p.id}" style="display:block;text-align:center;background:#1d4ed8;color:white;border-radius:8px;padding:6px;font-size:12px;font-weight:600;text-decoration:none;">View Details →</a>
      </div>`;

    L.marker([p.lat, p.lng], { icon: damacIcon })
      .bindPopup(popup, { maxWidth: 240 })
      .addTo(this.map);
  }
}
