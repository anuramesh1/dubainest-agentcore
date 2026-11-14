import { Injectable, signal } from "@angular/core";

export type Lang = "en" | "ar";

const translations: Record<Lang, Record<string, string>> = {
  en: {
    "nav.title": "DubaiNest AI",
    "nav.listings": "Properties",
    "nav.map": "Map",
    "nav.assistant": "Ask the AI Agents",
    "listings.title": "Dubai Properties",
    "listings.subtitle": "Find your perfect investment",
    "filter.area": "Area",
    "filter.builder": "Builder",
    "filter.type": "Type",
    "filter.bedrooms": "Bedrooms",
    "filter.minPrice": "Min Price",
    "filter.maxPrice": "Max Price",
    "filter.any": "Any",
    "filter.clear": "Clear Filters",
    "property.bed": "Bed",
    "property.bath": "Bath",
    "property.sqft": "sqft",
    "property.roi": "ROI",
    "property.viewDetails": "View Details",
    "property.enquire": "Enquire Now",
    "detail.overview": "Overview",
    "detail.amenities": "Amenities",
    "detail.roi": "ROI Summary",
    "detail.developer": "Developer",
    "detail.completion": "Completion",
    "detail.size": "Size",
    "detail.roiNote":
      "Estimated gross rental yield based on current market rates.",
    "chatbot.title": "AI Property Assistant",
    "chatbot.placeholder": "Ask about Dubai properties...",
    "chatbot.send": "Send",
    "chatbot.greeting":
      "Hello! I'm your AI property assistant. Ask me anything about Dubai real estate — prices, areas, ROI, or investment advice.",
    "type.apartment": "Apartment",
    "type.villa": "Villa",
    "type.townhouse": "Townhouse",
    "type.penthouse": "Penthouse",
    "type.studio": "Studio",
  },
  ar: {
    "nav.title": "دبي نست AI",
    "nav.listings": "العقارات",
    "nav.map": "الخريطة",
    "nav.assistant": "اسأل الوكلاء الأذكياء",
    "listings.title": "عقارات دبي",
    "listings.subtitle": "ابحث عن استثمارك المثالي",
    "filter.area": "المنطقة",
    "filter.builder": "المطوّر",
    "filter.type": "النوع",
    "filter.bedrooms": "غرف النوم",
    "filter.minPrice": "أدنى سعر",
    "filter.maxPrice": "أعلى سعر",
    "filter.any": "الكل",
    "filter.clear": "مسح الفلاتر",
    "property.bed": "غرفة",
    "property.bath": "حمام",
    "property.sqft": "قدم²",
    "property.roi": "العائد",
    "property.viewDetails": "عرض التفاصيل",
    "property.enquire": "استفسر الآن",
    "detail.overview": "نظرة عامة",
    "detail.amenities": "المرافق",
    "detail.roi": "ملخص العائد",
    "detail.developer": "المطور",
    "detail.completion": "سنة الإنجاز",
    "detail.size": "المساحة",
    "detail.roiNote":
      "عائد الإيجار الإجمالي المقدر بناءً على أسعار السوق الحالية.",
    "chatbot.title": "مساعد العقارات الذكي",
    "chatbot.placeholder": "اسأل عن عقارات دبي...",
    "chatbot.send": "إرسال",
    "chatbot.greeting":
      "مرحباً! أنا مساعدك الذكي للعقارات. اسألني عن أي شيء يتعلق بعقارات دبي.",
    "type.apartment": "شقة",
    "type.villa": "فيلا",
    "type.townhouse": "تاون هاوس",
    "type.penthouse": "بنتهاوس",
    "type.studio": "استوديو",
  },
};

@Injectable({ providedIn: "root" })
export class I18nService {
  lang = signal<Lang>("en");

  setLang(lang: Lang) {
    this.lang.set(lang);
  }

  t(key: string): string {
    return translations[this.lang()][key] ?? key;
  }
}
