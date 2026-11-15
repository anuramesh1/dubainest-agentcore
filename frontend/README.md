# Frontend — DubaiNest AI

Angular 21 + Tailwind single-page app: property listings with filters, a Leaflet map, a detail page with an enquiry form, and the chat panel that streams answers from the agent. English and Arabic.

```
src/app/
├── components/   # chatbot (SSE reader), markdown-response, navbar
├── pages/        # listings, map, property-detail
├── services/     # property, chat (session + visitor ids), i18n, signed-body (POST body hash for CloudFront)
├── models/
└── pipes/
```

```bash
npm ci
npm start        # http://localhost:4200, calls the api on :8000 (src/environments/environment.ts)
npm run build    # production build into dist/, uploaded by scripts/deploy.sh
```

Behind CloudFront, every POST carries `x-amz-content-sha256` (the SHA-256 of its body), because the api's Function URL only accepts requests CloudFront has signed. See the main [README](../README.md).
