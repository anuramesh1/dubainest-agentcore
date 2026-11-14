import { HttpInterceptorFn } from "@angular/common/http";
import { from, switchMap } from "rxjs";

// Behind CloudFront, /api/* goes to a Lambda Function URL that only accepts
// requests CloudFront has signed (origin access control). CloudFront cannot
// hash a request body, so for POST/PUT Lambda requires the browser to send the
// body's SHA-256 in x-amz-content-sha256. Harmless locally (uvicorn ignores it).
export const PAYLOAD_HASH_HEADER = "x-amz-content-sha256";

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(text),
  );
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** JSON body plus the headers a signed POST needs, for fetch(). */
export async function signedJson(
  payload: unknown,
): Promise<{ body: string; headers: Record<string, string> }> {
  const body = JSON.stringify(payload);
  return {
    body,
    headers: {
      "Content-Type": "application/json",
      [PAYLOAD_HASH_HEADER]: await sha256Hex(body),
    },
  };
}

/** The same for HttpClient: serialise the JSON body ourselves so the hash
 *  covers exactly the bytes that are sent. */
export const payloadHashInterceptor: HttpInterceptorFn = (req, next) => {
  const hasBody = ["POST", "PUT", "PATCH"].includes(req.method);
  if (!hasBody || req.body instanceof FormData || req.body instanceof Blob) {
    return next(req);
  }
  const body =
    typeof req.body === "string" ? req.body : JSON.stringify(req.body ?? {});
  return from(sha256Hex(body)).pipe(
    switchMap((hash) =>
      next(
        req.clone({
          body,
          setHeaders: {
            "Content-Type": "application/json",
            [PAYLOAD_HASH_HEADER]: hash,
          },
        }),
      ),
    ),
  );
};
