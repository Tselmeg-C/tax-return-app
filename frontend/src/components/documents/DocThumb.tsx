import { useState, type ReactNode } from "react";

/**
 * Thumbnail from `GET /api/documents/{id}/preview` (#35). The session cookie authenticates the
 * `<img>`. A document without a preview yet (404) shows `fallback`; `retryKey` (e.g. the status)
 * remounts the image so it is fetched again once processing has finished.
 * The alt text never contains the file name.
 */
export function DocThumb(props: { documentId: string; retryKey?: string; fallback: ReactNode }) {
  const [failed, setFailed] = useState<string | null>(null);
  const key = props.retryKey ?? "";
  if (failed === key) return <>{props.fallback}</>;
  return (
    <img
      key={key}
      src={`/api/documents/${props.documentId}/preview`}
      alt="Vorschau des Belegs"
      loading="lazy"
      width={40}
      height={40}
      className="h-10 w-10 shrink-0 rounded border object-cover"
      onError={() => setFailed(key)}
    />
  );
}
