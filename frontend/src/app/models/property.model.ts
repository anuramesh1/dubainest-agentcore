export interface Property {
  id: string;
  title: string;
  titleAr: string;
  area: string;
  areaAr: string;
  price: number;
  currency: string;
  type: "apartment" | "villa" | "townhouse" | "penthouse" | "studio";
  bedrooms: number;
  bathrooms: number;
  sizeSqft: number;
  images: string[];
  description: string;
  descriptionAr: string;
  amenities: string[];
  roi: number;
  developer: string;
  completionYear: number;
  lat: number;
  lng: number;
}

export interface PropertyFilters {
  area: string;
  developer: string;
  minPrice: number | null;
  maxPrice: number | null;
  type: string;
  bedrooms: number | null;
}

export interface PropertyFacets {
  areas: string[];
  developers: string[];
  types: string[];
  bedrooms: number[];
}
