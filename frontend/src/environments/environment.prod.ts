// Production build: the SPA is served from CloudFront, which proxies /api/* to
// the backend ALB (same origin). Using a relative apiUrl ("") means requests go
// to the same CloudFront domain and get routed to the backend — no hardcoded
// ALB DNS, so the backend can be torn down and recreated without rebuilding the
// frontend.
export const environment = {
  production: true,
  apiUrl: "",
};
