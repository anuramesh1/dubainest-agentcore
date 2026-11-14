# Frontend — DubaiNest AI

Angular 21+ SPA. Chatbot widget, property listings, Leaflet map view, Arabic/English toggle.

**Status:** Phase 2 frontend scaffold is now located directly under `frontend/`.

## Current structure

```
frontend/
├── src/
│   ├── app/
│   │   ├── core/               # services, interceptors
│   │   ├── shared/             # ui components
│   │   ├── features/
│   │   │   ├── chat/           # chatbot widget
│   │   │   ├── listings/       # property grid + filters
│   │   │   ├── property/       # detail page
│   │   │   └── map/            # Leaflet view
│   │   ├── app.config.ts
│   │   └── app.routes.ts
│   ├── assets/
│   └── locale/                 # i18n: en + ar
├── angular.json
├── tailwind.config.js
├── tsconfig.json
└── package.json
```
